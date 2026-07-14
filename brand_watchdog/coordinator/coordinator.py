"""Coordenador de ciclos de monitoramento de compliance.

Orquestra o novo fluxo distribuído:
1. Calcula versão do conjunto de regras (RuleSetVersionCalculator)
2. Cria ciclo no banco com status "dispatched"
3. Publica mensagens na fila SQS (SQSPublisher)
4. Inicia consolidação assíncrona (CycleConsolidator)

O processamento individual de sites é agora responsabilidade
dos Workers ECS que consomem da fila SQS.

Requirements: 1.1, 1.5, 7.1, 7.2, 7.4
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from brand_watchdog.config import AppConfig
from brand_watchdog.coordinator.cycle_consolidator import (
    CycleConsolidator,
)
from brand_watchdog.models.database import get_session
from brand_watchdog.models.dataclasses import (
    CycleResult,
    TargetSite,
)
from brand_watchdog.models.entities import MonitoringCycleModel
from brand_watchdog.queue.messages import ProcessingMessage
from brand_watchdog.queue.publisher import SQSPublisher
from brand_watchdog.registry.target_site_manager import (
    TargetSiteManager,
)
from brand_watchdog.utils.rule_set_version import (
    RuleSetDirectoryError,
    RuleSetVersionCalculator,
)

logger = logging.getLogger(__name__)


class MonitoringCoordinator:
    """Coordena ciclos de monitoramento de compliance (distribuído).

    Responsabilidades:
        - Calcular versão do conjunto de regras no início do ciclo
        - Publicar mensagens na fila SQS para Workers ECS
        - Iniciar consolidação assíncrona dos resultados
        - Garantir que apenas um ciclo executa por vez (lock)
        - Persistir ciclo e estatísticas no banco de dados
        - Logar início, fim e resultados do ciclo

    Args:
        rule_set_calculator: Calculador de versão de regras.
        sqs_publisher: Publisher de mensagens na fila SQS.
        consolidator: Consolidador de resultados do ciclo.
        target_site_manager: Gerenciador de sites-alvo.
        config: Configuração da aplicação.
    """

    def __init__(
        self,
        rule_set_calculator: RuleSetVersionCalculator,
        sqs_publisher: SQSPublisher,
        consolidator: CycleConsolidator,
        target_site_manager: TargetSiteManager,
        config: AppConfig,
    ) -> None:
        self._rule_set_calculator = rule_set_calculator
        self._sqs_publisher = sqs_publisher
        self._consolidator = consolidator
        self._target_site_manager = target_site_manager
        self._config = config
        self._cycle_running = False
        self._lock = asyncio.Lock()
        self._previous_rule_version: str | None = None

    async def run_cycle(self) -> CycleResult:
        """Executa um ciclo completo de monitoramento (distribuído).

        Novo fluxo:
            1. Verifica lock de ciclo (se já em execução, pula)
            2. Calcula versão do conjunto de regras
            3. Cria ciclo no banco com rule_set_version
            4. Obtém Target Sites ativos
            5. Publica mensagens na fila SQS
            6. Atualiza ciclo com status "dispatched"
            7. Inicia consolidação assíncrona (background)

        Returns:
            CycleResult com estatísticas do ciclo iniciado.
            Se o ciclo foi pulado (lock), retorna CycleResult
            com sites_processed=0 e status refletindo o skip.
        """
        if self._is_cycle_running():
            logger.warning(
                "Ciclo de monitoramento pulado: ciclo anterior "
                "ainda em execução"
            )
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

        async with self._lock:
            self._cycle_running = True

        try:
            return await self._execute_cycle()
        finally:
            async with self._lock:
                self._cycle_running = False

    async def _execute_cycle(self) -> CycleResult:
        """Executa o ciclo de monitoramento distribuído.

        Fluxo:
            1. Calcula rule_set_version
            2. Verifica mudança de versão e loga
            3. Cria ciclo no banco
            4. Obtém sites ativos
            5. Cria ProcessingMessages
            6. Publica na fila via SQSPublisher
            7. Atualiza ciclo com status "dispatched"
            8. Inicia consolidação em background

        Returns:
            CycleResult com estatísticas do despacho.
        """
        cycle_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        logger.info(
            "Iniciando ciclo de monitoramento: id=%s, start=%s",
            cycle_id,
            started_at.isoformat(),
        )

        # 1. Calcular versão do conjunto de regras
        try:
            rule_version = self._rule_set_calculator.calculate()
        except RuleSetDirectoryError as exc:
            logger.error(
                "Erro ao calcular versão de regras: %s. "
                "Ciclo abortado.",
                str(exc),
            )
            await self._create_cycle_record(
                cycle_id=cycle_id,
                started_at=started_at,
                status=MonitoringCycleModel.STATUS_ERROR,
            )
            return CycleResult(
                cycle_id=cycle_id,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc),
                sites_processed=0,
                sites_failed=0,
                detections_found=0,
                site_results=[],
            )

        # 2. Verificar mudança de versão e logar
        if self._rule_set_calculator.has_changed(
            self._previous_rule_version
        ):
            logger.info(
                "Mudança de versão de regras: %s -> %s",
                self._previous_rule_version,
                rule_version,
            )
        self._previous_rule_version = rule_version

        # 3. Criar ciclo no banco com rule_set_version
        await self._create_cycle_record(
            cycle_id=cycle_id,
            started_at=started_at,
            status=MonitoringCycleModel.STATUS_DISPATCHED,
            rule_set_version=rule_version,
        )

        # 4. Obter sites ativos
        target_sites = await self._target_site_manager.list_all()

        if not target_sites:
            logger.warning(
                "Nenhum Target Site ativo encontrado. "
                "Ciclo encerrado sem processamento."
            )
            ended_at = datetime.now(timezone.utc)
            await self._update_cycle_record(
                cycle_id=cycle_id,
                ended_at=ended_at,
                sites_processed=0,
                sites_failed=0,
                detections_found=0,
                status=MonitoringCycleModel.STATUS_COMPLETED,
            )
            return CycleResult(
                cycle_id=cycle_id,
                started_at=started_at,
                ended_at=ended_at,
                sites_processed=0,
                sites_failed=0,
                detections_found=0,
                site_results=[],
            )

        # 5. Criar ProcessingMessages para cada site
        messages = self._build_messages(
            target_sites=target_sites,
            cycle_id=cycle_id,
            rule_version=rule_version,
        )

        # 6. Publicar mensagens via SQSPublisher
        success_count, failure_count = (
            await self._sqs_publisher.publish_all(messages)
        )

        sites_dispatched = len(target_sites)

        # 7. Atualizar ciclo com status "dispatched"
        await self._update_cycle_dispatched(
            cycle_id=cycle_id,
            sites_dispatched=sites_dispatched,
            sites_failed=failure_count,
        )

        logger.info(
            "Ciclo despachado: id=%s, sites_dispatched=%d, "
            "publicados=%d, falhas=%d, rule_version=%s",
            cycle_id,
            sites_dispatched,
            success_count,
            failure_count,
            rule_version,
        )

        # 8. Iniciar consolidação em background (non-blocking)
        asyncio.create_task(
            self._consolidator.consolidate(
                cycle_id=cycle_id,
                sites_dispatched=sites_dispatched,
            )
        )

        return CycleResult(
            cycle_id=cycle_id,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            sites_processed=success_count,
            sites_failed=failure_count,
            detections_found=0,
            site_results=[],
        )

    def _build_messages(
        self,
        target_sites: list[TargetSite],
        cycle_id: str,
        rule_version: str,
    ) -> list[ProcessingMessage]:
        """Cria lista de ProcessingMessage para cada site ativo.

        Args:
            target_sites: Lista de sites-alvo ativos.
            cycle_id: ID do ciclo de monitoramento.
            rule_version: Versão do conjunto de regras.

        Returns:
            Lista de ProcessingMessage para publicação na fila.
        """
        return [
            ProcessingMessage(
                site_id=site.id,
                cycle_id=cycle_id,
                brand=site.brand,
                url=site.url,
                rule_set_version=rule_version,
            )
            for site in target_sites
        ]

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
        rule_set_version: str | None = None,
    ) -> None:
        """Cria registro de MonitoringCycleModel no banco.

        Args:
            cycle_id: ID único do ciclo.
            started_at: Timestamp de início do ciclo.
            status: Status inicial do ciclo.
            rule_set_version: Versão do conjunto de regras.
        """
        async with get_session() as session:
            cycle_model = MonitoringCycleModel(
                id=cycle_id,
                started_at=started_at,
                status=status,
                rule_set_version=rule_set_version,
            )
            session.add(cycle_model)
            await session.flush()

        logger.debug(
            "Registro de ciclo criado: id=%s, status=%s, "
            "rule_set_version=%s",
            cycle_id,
            status,
            rule_set_version,
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
        """Atualiza registro de MonitoringCycleModel com stats.

        Args:
            cycle_id: ID do ciclo a atualizar.
            ended_at: Timestamp de término do ciclo.
            sites_processed: Número de sites com sucesso.
            sites_failed: Número de sites que falharam.
            detections_found: Total de detecções encontradas.
            status: Status final do ciclo.
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

    async def _update_cycle_dispatched(
        self,
        cycle_id: str,
        sites_dispatched: int,
        sites_failed: int = 0,
    ) -> None:
        """Atualiza ciclo com informações de despacho.

        Registra o número de sites despachados e falhas de
        publicação, mantendo status "dispatched".

        Args:
            cycle_id: ID do ciclo a atualizar.
            sites_dispatched: Total de sites despachados.
            sites_failed: Falhas na publicação SQS.
        """
        from sqlalchemy import select

        async with get_session() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_id
            )
            result = await session.execute(stmt)
            cycle_model = result.scalar_one_or_none()

            if cycle_model is not None:
                cycle_model.sites_dispatched = sites_dispatched
                cycle_model.sites_failed = sites_failed
                cycle_model.status = (
                    MonitoringCycleModel.STATUS_DISPATCHED
                )

        logger.debug(
            "Ciclo atualizado para dispatched: id=%s, "
            "sites_dispatched=%d, sites_failed=%d",
            cycle_id,
            sites_dispatched,
            sites_failed,
        )
