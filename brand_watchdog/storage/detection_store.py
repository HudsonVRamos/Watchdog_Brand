"""Armazenamento de resultados de detecção.

Gerencia o ciclo de vida de resultados de detecção de marca:
- Persistência com retry e exponential backoff (3 tentativas)
- Consulta paginada com filtros (target_url, date range, match_type)
- Cálculo de expiração baseado em detection_retention_days
- Limpeza automática de resultados expirados
- Recuperação de detecções do ciclo anterior para supressão de duplicatas
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import get_session
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    DetectionResult,
    QueryResult,
)
from brand_watchdog.models.entities import DetectionResultModel

logger = logging.getLogger(__name__)

# Tamanho máximo de página para consultas
_MAX_PAGE_SIZE = 100

# Tamanho do batch para operações de cleanup
_CLEANUP_BATCH_SIZE = 100


class DetectionStore:
    """Gerencia persistência e consulta de resultados de detecção.

    Responsabilidades:
        - Persistir detecções com retry e exponential backoff
        - Consultar detecções com filtros e paginação
        - Calcular expires_at baseado em detection_retention_days
        - Remover detecções expiradas
        - Recuperar detecções do ciclo anterior para supressão

    Args:
        config: Configuração de storage com retention days.
    """

    def __init__(self, config: StorageConfig) -> None:
        self._retention_days = config.detection_retention_days

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type((OSError, ConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def save(
        self,
        detection: DetectionResult,
        target_site_id: str | None = None,
        monitoring_cycle_id: str = "",
    ) -> str:
        """Persiste resultado de detecção com retry.

        Calcula expires_at como detected_at + detection_retention_days.
        Retry automático com backoff exponencial: 1s, 2s, 4s (3 tentativas).

        Args:
            detection: Resultado de detecção a ser persistido.
            target_site_id: ID do target site (UUID). Se None, usa target_url.
            monitoring_cycle_id: ID do ciclo de monitoramento.

        Returns:
            ID único do registro persistido.

        Raises:
            Exception: Se todas as tentativas de persistência falharem.
        """
        detection_id = str(uuid.uuid4())
        expires_at = detection.detected_at + timedelta(
            days=self._retention_days
        )

        async with get_session() as session:
            model = DetectionResultModel(
                id=detection_id,
                target_site_id=target_site_id or detection.target_url,
                screenshot_id=detection.screenshot_ref_id,
                monitoring_cycle_id=monitoring_cycle_id,
                match_type=detection.match_type,
                confidence=detection.confidence,
                bbox_x_percent=detection.bounding_box.x_percent,
                bbox_y_percent=detection.bounding_box.y_percent,
                bbox_width_percent=(
                    detection.bounding_box.width_percent
                ),
                bbox_height_percent=(
                    detection.bounding_box.height_percent
                ),
                description=detection.description,
                detected_at=detection.detected_at,
                expires_at=expires_at,
            )
            session.add(model)
            await session.flush()

        logger.info(
            "Detecção persistida: id=%s, target=%s, tipo=%s, "
            "confiança=%d, expira_em=%s",
            detection_id,
            detection.target_url,
            detection.match_type,
            detection.confidence,
            expires_at.isoformat(),
        )
        return detection_id

    async def query(
        self,
        target_url: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        match_type: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> QueryResult:
        """Consulta resultados com filtros e paginação.

        Retorna resultados em ordem cronológica reversa
        (mais recentes primeiro).
        page_size é limitado a 100 resultados por página.

        Args:
            target_url: Filtrar por URL do site-alvo.
            start_date: Filtrar detecções a partir desta data.
            end_date: Filtrar detecções até esta data.
            match_type: Filtrar por tipo de match ("logo" ou "text").
            page: Número da página (mínimo 1).
            page_size: Resultados por página (máximo 100).

        Returns:
            QueryResult com resultados paginados e metadados.
        """
        # Garante limites de paginação
        page = max(1, page)
        page_size = min(max(1, page_size), _MAX_PAGE_SIZE)

        # Constrói filtros base
        conditions = self._build_filter_conditions(
            target_url, start_date, end_date, match_type
        )

        async with get_session() as session:
            # Conta total de resultados
            count_stmt = select(func.count(DetectionResultModel.id))
            for condition in conditions:
                count_stmt = count_stmt.where(condition)
            count_result = await session.execute(count_stmt)
            total_count = count_result.scalar() or 0

            if total_count == 0:
                logger.debug(
                    "Nenhum resultado encontrado para os filtros: "
                    "target_url=%s, start_date=%s, end_date=%s, "
                    "match_type=%s",
                    target_url,
                    start_date,
                    end_date,
                    match_type,
                )
                return QueryResult(
                    results=[],
                    total_count=0,
                    page=page,
                    page_size=page_size,
                    has_next=False,
                )

            # Consulta paginada em ordem cronológica reversa
            offset = (page - 1) * page_size
            query_stmt = (
                select(DetectionResultModel)
                .order_by(DetectionResultModel.detected_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            for condition in conditions:
                query_stmt = query_stmt.where(condition)

            result = await session.execute(query_stmt)
            models = result.scalars().all()

        # Converte modelos para dataclasses
        results = [self._model_to_dataclass(m) for m in models]
        has_next = (page * page_size) < total_count

        return QueryResult(
            results=results,
            total_count=total_count,
            page=page,
            page_size=page_size,
            has_next=has_next,
        )

    async def cleanup_expired(self) -> int:
        """Remove resultados expirados conforme retention period.

        Processa em batches para não sobrecarregar o banco.

        Returns:
            Número total de detecções removidas.
        """
        now = datetime.now(timezone.utc)
        total_removed = 0

        while True:
            removed_in_batch = await self._cleanup_batch(now)
            total_removed += removed_in_batch

            if removed_in_batch < _CLEANUP_BATCH_SIZE:
                break

        if total_removed > 0:
            logger.info(
                "Cleanup de detecções concluído: %d removidas",
                total_removed,
            )

        return total_removed

    async def get_previous_cycle_detections(
        self, target_url: str
    ) -> list[DetectionResult]:
        """Retorna detecções do ciclo anterior para supressão de duplicatas.

        Busca as detecções mais recentes do target_url que pertencem
        ao ciclo de monitoramento anterior (distinto do mais recente).

        Args:
            target_url: URL do site-alvo.

        Returns:
            Lista de DetectionResult do ciclo anterior.
        """
        async with get_session() as session:
            # Busca o ciclo mais recente para este target
            latest_cycle_stmt = (
                select(DetectionResultModel.monitoring_cycle_id)
                .where(
                    DetectionResultModel.target_site_id == target_url
                )
                .order_by(DetectionResultModel.detected_at.desc())
                .limit(1)
            )
            latest_result = await session.execute(latest_cycle_stmt)
            latest_cycle_id = latest_result.scalar_one_or_none()

            if latest_cycle_id is None:
                return []

            # Busca o ciclo anterior ao mais recente
            prev_cycle_stmt = (
                select(DetectionResultModel.monitoring_cycle_id)
                .where(
                    DetectionResultModel.target_site_id == target_url,
                    DetectionResultModel.monitoring_cycle_id
                    != latest_cycle_id,
                )
                .order_by(DetectionResultModel.detected_at.desc())
                .limit(1)
            )
            prev_result = await session.execute(prev_cycle_stmt)
            prev_cycle_id = prev_result.scalar_one_or_none()

            if prev_cycle_id is None:
                return []

            # Busca todas as detecções do ciclo anterior
            detections_stmt = (
                select(DetectionResultModel)
                .where(
                    DetectionResultModel.target_site_id == target_url,
                    DetectionResultModel.monitoring_cycle_id
                    == prev_cycle_id,
                )
                .order_by(DetectionResultModel.detected_at.desc())
            )
            detections_result = await session.execute(detections_stmt)
            models = detections_result.scalars().all()

        return [self._model_to_dataclass(m) for m in models]

    async def _cleanup_batch(self, now: datetime) -> int:
        """Remove um batch de detecções expiradas.

        Args:
            now: Datetime UTC atual para comparação com expires_at.

        Returns:
            Número de detecções removidas neste batch.
        """
        async with get_session() as session:
            # Busca IDs expirados neste batch
            stmt = (
                select(DetectionResultModel)
                .where(DetectionResultModel.expires_at <= now)
                .limit(_CLEANUP_BATCH_SIZE)
            )
            result = await session.execute(stmt)
            expired_models = result.scalars().all()

            if not expired_models:
                return 0

            for model in expired_models:
                await session.delete(model)

            logger.debug(
                "Batch de cleanup: %d detecções removidas",
                len(expired_models),
            )

        return len(expired_models)

    @staticmethod
    def _build_filter_conditions(
        target_url: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        match_type: str | None,
    ) -> list:
        """Constrói lista de condições SQLAlchemy para filtros.

        Args:
            target_url: Filtrar por URL do site-alvo.
            start_date: Filtrar a partir desta data.
            end_date: Filtrar até esta data.
            match_type: Filtrar por tipo de match.

        Returns:
            Lista de condições SQLAlchemy.
        """
        conditions = []

        if target_url is not None:
            conditions.append(
                DetectionResultModel.target_site_id == target_url
            )
        if start_date is not None:
            conditions.append(
                DetectionResultModel.detected_at >= start_date
            )
        if end_date is not None:
            conditions.append(
                DetectionResultModel.detected_at <= end_date
            )
        if match_type is not None:
            conditions.append(
                DetectionResultModel.match_type == match_type
            )

        return conditions

    @staticmethod
    def _model_to_dataclass(model: DetectionResultModel) -> DetectionResult:
        """Converte um DetectionResultModel para DetectionResult dataclass.

        Args:
            model: Modelo SQLAlchemy a ser convertido.

        Returns:
            DetectionResult dataclass correspondente.
        """
        return DetectionResult(
            target_url=model.target_site_id,
            match_type=model.match_type,
            confidence=model.confidence,
            bounding_box=BoundingBox(
                x_percent=model.bbox_x_percent,
                y_percent=model.bbox_y_percent,
                width_percent=model.bbox_width_percent,
                height_percent=model.bbox_height_percent,
            ),
            description=model.description,
            detected_at=model.detected_at,
            screenshot_ref_id=model.screenshot_id,
        )
