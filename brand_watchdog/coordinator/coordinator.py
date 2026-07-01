"""Coordenador de ciclos de monitoramento.

Orquestra o fluxo completo: capture → analyze → alert para cada
Target Site ativo, com lock de ciclo para evitar execuções sobrepostas,
persistência de resultados e logging de estatísticas.

Requirements: 5.1, 5.3, 5.4, 5.5, 5.6
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from brand_watchdog.alerts.alert_service import AlertService
from brand_watchdog.analyzer.analyzer import Analyzer
from brand_watchdog.config import AppConfig
from brand_watchdog.crawler.crawler import Crawler
from brand_watchdog.models.database import get_session
from brand_watchdog.models.dataclasses import (
    CycleResult,
    SiteResult,
    TargetSite,
)
from brand_watchdog.models.entities import MonitoringCycleModel
from brand_watchdog.registry.brand_registry import BrandRegistry
from brand_watchdog.registry.target_site_manager import TargetSiteManager
from brand_watchdog.storage.detection_store import DetectionStore
from brand_watchdog.storage.screenshot_store import ScreenshotStore

logger = logging.getLogger(__name__)


class MonitoringCoordinator:
    """Coordena ciclos completos de monitoramento de marca.

    Responsabilidades:
        - Executar ciclos de monitoramento (capture → analyze → alert)
        - Garantir que apenas um ciclo executa por vez (lock)
        - Processar todos os Target Sites ativos com isolamento de falha
        - Persistir ciclo e estatísticas no banco de dados
        - Logar início, fim e resultados do ciclo

    Args:
        crawler: Instância do Crawler para captura de screenshots.
        analyzer: Instância do Analyzer para detecção de marca.
        alert_service: Instância do AlertService para notificações.
        detection_store: Store para persistência de detecções.
        screenshot_store: Store para persistência de screenshots.
        brand_registry: Registro de ativos de marca.
        target_site_manager: Gerenciador de sites-alvo.
        config: Configuração da aplicação.
    """

    def __init__(
        self,
        crawler: Crawler,
        analyzer: Analyzer,
        alert_service: AlertService,
        detection_store: DetectionStore,
        screenshot_store: ScreenshotStore,
        brand_registry: BrandRegistry,
        target_site_manager: TargetSiteManager,
        config: AppConfig,
    ) -> None:
        self._crawler = crawler
        self._analyzer = analyzer
        self._alert_service = alert_service
        self._detection_store = detection_store
        self._screenshot_store = screenshot_store
        self._brand_registry = brand_registry
        self._target_site_manager = target_site_manager
        self._config = config
        self._cycle_running = False
        self._lock = asyncio.Lock()

    async def run_cycle(self) -> CycleResult:
        """Executa um ciclo completo de monitoramento.

        Fluxo:
            1. Verifica lock de ciclo (se já em execução, pula)
            2. Cria MonitoringCycleModel no banco com status="running"
            3. Obtém todos os Target Sites ativos e Brand Assets
            4. Processa cada site (capture → analyze → alert)
            5. Atualiza ciclo com stats e status="completed"
            6. Loga resumo completo do ciclo

        Returns:
            CycleResult com estatísticas do ciclo executado.
            Se o ciclo foi pulado (lock), retorna CycleResult
            com sites_processed=0 e status refletindo o skip.
        """
        # Verifica se um ciclo já está em execução (Req 5.4)
        if self._is_cycle_running():
            logger.warning(
                "Ciclo de monitoramento pulado: ciclo anterior "
                "ainda em execução"
            )
            # Registra ciclo pulado no banco
            skipped_cycle_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            await self._create_cycle_record(
                cycle_id=skipped_cycle_id,
                started_at=now,
                status="skipped",
            )
            await self._update_cycle_record(
                cycle_id=skipped_cycle_id,
                ended_at=now,
                sites_processed=0,
                sites_failed=0,
                detections_found=0,
                status="skipped",
            )
            return CycleResult(
                cycle_id=skipped_cycle_id,
                started_at=now,
                ended_at=now,
                sites_processed=0,
                sites_failed=0,
                detections_found=0,
                site_results=[],
            )

        # Adquire lock de ciclo
        async with self._lock:
            self._cycle_running = True

        try:
            return await self._execute_cycle()
        finally:
            async with self._lock:
                self._cycle_running = False

    async def _execute_cycle(self) -> CycleResult:
        """Executa o ciclo de monitoramento propriamente dito.

        Separado de run_cycle para manter a lógica de lock limpa.

        Returns:
            CycleResult com estatísticas completas.
        """
        cycle_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        logger.info(
            "Iniciando ciclo de monitoramento: id=%s, start=%s",
            cycle_id,
            started_at.isoformat(),
        )

        # Cria registro do ciclo no banco (Req 5.6)
        await self._create_cycle_record(
            cycle_id=cycle_id,
            started_at=started_at,
            status="running",
        )

        # Obtém Target Sites ativos e Brand Assets (Req 5.3)
        target_sites = await self._target_site_manager.list_all()
        brand_assets = await self._brand_registry.get_all_assets()

        if not target_sites:
            logger.warning(
                "Nenhum Target Site ativo encontrado. "
                "Ciclo encerrado sem processamento."
            )

        if not brand_assets:
            logger.warning(
                "Nenhum Brand Asset registrado. "
                "Análise não será executada."
            )

        # Processa cada site (Req 5.3, 5.5)
        site_results: list[SiteResult] = []
        sites_processed = 0
        sites_failed = 0
        total_detections = 0

        for target_site in target_sites:
            site_result = await self._process_site(
                target_site=target_site,
                brand_assets=brand_assets,
                cycle_id=cycle_id,
            )
            site_results.append(site_result)

            if site_result.success:
                sites_processed += 1
            else:
                sites_failed += 1

            total_detections += len(site_result.detections)

        # Atualiza ciclo com stats finais (Req 5.6)
        ended_at = datetime.now(timezone.utc)
        await self._update_cycle_record(
            cycle_id=cycle_id,
            ended_at=ended_at,
            sites_processed=sites_processed,
            sites_failed=sites_failed,
            detections_found=total_detections,
            status="completed",
        )

        # Log de ciclo completo (Req 5.6)
        logger.info(
            "Ciclo de monitoramento concluído: id=%s, "
            "start=%s, end=%s, sites_processed=%d, "
            "sites_failed=%d, detections_found=%d",
            cycle_id,
            started_at.isoformat(),
            ended_at.isoformat(),
            sites_processed,
            sites_failed,
            total_detections,
        )

        return CycleResult(
            cycle_id=cycle_id,
            started_at=started_at,
            ended_at=ended_at,
            sites_processed=sites_processed,
            sites_failed=sites_failed,
            detections_found=total_detections,
            site_results=site_results,
        )

    async def _process_site(
        self,
        target_site: TargetSite,
        brand_assets: list,
        cycle_id: str,
    ) -> SiteResult:
        """Processa um site individual: capture → analyze → alert.

        Se qualquer etapa falhar com exceção, registra o erro e
        retorna SiteResult com success=False, permitindo que o
        ciclo continue com os demais sites (Req 5.5).

        Args:
            target_site: Site-alvo a ser processado.
            brand_assets: Lista de ativos de marca para análise.
            cycle_id: ID do ciclo de monitoramento atual.

        Returns:
            SiteResult com status de sucesso/falha e detecções.
        """
        logger.info(
            "Processando site: url=%s, id=%s",
            target_site.url,
            target_site.id,
        )

        try:
            # Etapa 1: Capture (Req 3.x)
            capture_result = await self._crawler.capture(
                target_site.url
            )

            if not capture_result.success:
                logger.warning(
                    "Falha na captura de %s: %s",
                    target_site.url,
                    capture_result.error_message,
                )
                return SiteResult(
                    target_url=target_site.url,
                    success=False,
                    detections=[],
                    error_message=capture_result.error_message,
                )

            # Armazena screenshot via ScreenshotStore
            screenshot_model = await self._screenshot_store.store(
                png_bytes=capture_result.screenshot_path.read_bytes(),
                target_site_id=target_site.id,
                cycle_id=cycle_id,
                height_px=capture_result.page_height_px,
                was_truncated=capture_result.was_truncated,
            )
            screenshot_id = screenshot_model.id

            # Etapa 2: Analyze (Req 4.x)
            if not brand_assets:
                logger.info(
                    "Sem brand assets para análise de %s",
                    target_site.url,
                )
                return SiteResult(
                    target_url=target_site.url,
                    success=True,
                    detections=[],
                )

            detections = await self._analyzer.analyze(
                screenshot_path=capture_result.screenshot_path,
                brand_assets=brand_assets,
                target_url=target_site.url,
                screenshot_ref_id=screenshot_id,
            )

            # Persiste detecções no store
            for detection in detections:
                await self._detection_store.save(
                    detection,
                    target_site_id=target_site.id,
                    monitoring_cycle_id=cycle_id,
                )

            # Etapa 3: Alert (Req 6.x)
            confidence_threshold = (
                self._config.analyzer.confidence_threshold
            )
            recipients = self._config.alert.recipients

            if recipients:
                for detection in detections:
                    if detection.confidence >= confidence_threshold:
                        await self._alert_service.send_alert(
                            detection=detection,
                            recipients=recipients,
                        )

            logger.info(
                "Site processado com sucesso: url=%s, "
                "detections=%d",
                target_site.url,
                len(detections),
            )

            return SiteResult(
                target_url=target_site.url,
                success=True,
                detections=detections,
            )

        except Exception as exc:
            # Falha isolada — não interrompe ciclo (Req 5.5)
            logger.error(
                "Erro ao processar site %s: %s",
                target_site.url,
                str(exc),
                exc_info=True,
            )
            return SiteResult(
                target_url=target_site.url,
                success=False,
                detections=[],
                error_message=str(exc),
            )

    def _is_cycle_running(self) -> bool:
        """Verifica se um ciclo está em execução (lock).

        Usa flag interna protegida por asyncio.Lock para
        garantir thread-safety na verificação.

        Returns:
            True se um ciclo está em andamento.
        """
        return self._cycle_running

    async def _create_cycle_record(
        self,
        cycle_id: str,
        started_at: datetime,
        status: str = "running",
    ) -> None:
        """Cria registro de MonitoringCycleModel no banco.

        Args:
            cycle_id: ID único do ciclo.
            started_at: Timestamp de início do ciclo.
            status: Status inicial ("running" ou "skipped").
        """
        async with get_session() as session:
            cycle_model = MonitoringCycleModel(
                id=cycle_id,
                started_at=started_at,
                status=status,
            )
            session.add(cycle_model)
            await session.flush()

        logger.debug(
            "Registro de ciclo criado: id=%s, status=%s",
            cycle_id,
            status,
        )

    async def _update_cycle_record(
        self,
        cycle_id: str,
        ended_at: datetime,
        sites_processed: int,
        sites_failed: int,
        detections_found: int,
        status: str = "completed",
    ) -> None:
        """Atualiza registro de MonitoringCycleModel com stats finais.

        Args:
            cycle_id: ID do ciclo a atualizar.
            ended_at: Timestamp de término do ciclo.
            sites_processed: Número de sites processados com sucesso.
            sites_failed: Número de sites que falharam.
            detections_found: Total de detecções encontradas.
            status: Status final ("completed" ou "skipped").
        """
        from sqlalchemy import select

        async with get_session() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_id
            )
            result = await session.execute(stmt)
            cycle_model = result.scalar_one_or_none()

            if cycle_model is not None:
                cycle_model.ended_at = ended_at
                cycle_model.sites_processed = sites_processed
                cycle_model.sites_failed = sites_failed
                cycle_model.detections_found = detections_found
                cycle_model.status = status

        logger.debug(
            "Registro de ciclo atualizado: id=%s, status=%s, "
            "processed=%d, failed=%d, detections=%d",
            cycle_id,
            status,
            sites_processed,
            sites_failed,
            detections_found,
        )
