"""Coordenador de ciclos de monitoramento de compliance.

Orquestra o fluxo completo: capture → analyze_compliance → notify
para cada Target Site ativo, com lock de ciclo para evitar
execuções sobrepostas, persistência de resultados e logging
de estatísticas.

Requirements: 1.1, 8.1, 9.1
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from brand_watchdog.alerts.compliance_email_notifier import (
    ComplianceEmailNotifier,
)
from brand_watchdog.analyzer.compliance_analyzer import ComplianceAnalyzer
from brand_watchdog.config import AppConfig
from brand_watchdog.crawler.crawler import Crawler
from brand_watchdog.models.database import get_session
from brand_watchdog.models.dataclasses import (
    ComplianceReport,
    CycleResult,
    SiteResult,
    TargetSite,
)
from brand_watchdog.models.entities import MonitoringCycleModel
from brand_watchdog.registry.target_site_manager import TargetSiteManager
from brand_watchdog.storage.detection_store import DetectionStore
from brand_watchdog.storage.screenshot_store import ScreenshotStore

logger = logging.getLogger(__name__)


class MonitoringCoordinator:
    """Coordena ciclos completos de monitoramento de compliance.

    Responsabilidades:
        - Executar ciclos de monitoramento
          (capture → analyze_compliance → notify)
        - Garantir que apenas um ciclo executa por vez (lock)
        - Processar todos os Target Sites ativos com isolamento
          de falha
        - Persistir ciclo e estatísticas no banco de dados
        - Logar início, fim e resultados do ciclo

    Args:
        crawler: Instância do Crawler para captura de screenshots.
        compliance_analyzer: Instância do ComplianceAnalyzer para
            validação de compliance.
        compliance_notifier: Instância do ComplianceEmailNotifier
            para envio de relatórios.
        detection_store: Store para persistência de detecções.
        screenshot_store: Store para persistência de screenshots.
        target_site_manager: Gerenciador de sites-alvo.
        config: Configuração da aplicação.
    """

    def __init__(
        self,
        crawler: Crawler,
        compliance_analyzer: ComplianceAnalyzer,
        compliance_notifier: ComplianceEmailNotifier,
        detection_store: DetectionStore,
        screenshot_store: ScreenshotStore,
        target_site_manager: TargetSiteManager,
        config: AppConfig,
    ) -> None:
        self._crawler = crawler
        self._compliance_analyzer = compliance_analyzer
        self._compliance_notifier = compliance_notifier
        self._detection_store = detection_store
        self._screenshot_store = screenshot_store
        self._target_site_manager = target_site_manager
        self._config = config
        self._cycle_running = False
        self._lock = asyncio.Lock()

    async def run_cycle(self) -> CycleResult:
        """Executa um ciclo completo de monitoramento.

        Fluxo:
            1. Verifica lock de ciclo (se já em execução, pula)
            2. Cria MonitoringCycleModel no banco com status="running"
            3. Obtém todos os Target Sites ativos
            4. Processa cada site
               (capture → analyze_compliance → notify)
            5. Atualiza ciclo com stats e status="completed"
            6. Loga resumo completo do ciclo

        Returns:
            CycleResult com estatísticas do ciclo executado.
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

        await self._create_cycle_record(
            cycle_id=cycle_id,
            started_at=started_at,
            status="running",
        )

        target_sites = await self._target_site_manager.list_all()

        if not target_sites:
            logger.warning(
                "Nenhum Target Site ativo encontrado. "
                "Ciclo encerrado sem processamento."
            )

        site_results: list[SiteResult] = []
        cycle_reports: list[ComplianceReport] = []
        sites_processed = 0
        sites_failed = 0
        total_detections = 0

        for target_site in target_sites:
            site_result, report = await self._process_site(
                target_site=target_site,
                cycle_id=cycle_id,
            )
            site_results.append(site_result)

            if site_result.success:
                sites_processed += 1
                if report is not None:
                    cycle_reports.append(report)
            else:
                sites_failed += 1

            total_detections += len(site_result.detections)

        # Enviar email consolidado do ciclo (1 único email)
        recipients = self._config.alert.recipients
        if recipients and cycle_reports:
            await self._compliance_notifier.send_cycle_report(
                reports=cycle_reports,
                recipients=recipients,
            )

        ended_at = datetime.now(timezone.utc)
        await self._update_cycle_record(
            cycle_id=cycle_id,
            ended_at=ended_at,
            sites_processed=sites_processed,
            sites_failed=sites_failed,
            detections_found=total_detections,
            status="completed",
        )

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
        cycle_id: str,
    ) -> tuple[SiteResult, ComplianceReport | None]:
        """Processa um site: capture → analyze_compliance.

        Fluxo:
            1. Captura screenshot via Crawler
            2. Armazena screenshot via ScreenshotStore
            3. Chama compliance_analyzer.analyze_compliance()
               → ComplianceReport
            4. Retorna (SiteResult, ComplianceReport)

        O envio de email é feito de forma consolidada no final
        do ciclo (1 email com todos os sites).

        Se qualquer etapa falhar com exceção, registra o erro e
        retorna (SiteResult(success=False), None), permitindo que
        o ciclo continue com os demais sites.

        Args:
            target_site: Site-alvo a ser processado.
            cycle_id: ID do ciclo de monitoramento atual.

        Returns:
            Tupla (SiteResult, ComplianceReport | None).
        """
        logger.info(
            "Processando site: url=%s, id=%s",
            target_site.url,
            target_site.id,
        )

        try:
            # Etapa 1: Captura de screenshot
            capture_result = await self._crawler.capture(
                target_site.url
            )

            if not capture_result.success:
                logger.warning(
                    "Falha na captura de %s: %s",
                    target_site.url,
                    capture_result.error_message,
                )
                return (
                    SiteResult(
                        target_url=target_site.url,
                        success=False,
                        detections=[],
                        error_message=capture_result.error_message,
                    ),
                    None,
                )

            # Etapa 2: Armazena screenshot
            screenshot_model = await self._screenshot_store.store(
                png_bytes=(
                    capture_result.screenshot_path.read_bytes()
                ),
                target_site_id=target_site.id,
                cycle_id=cycle_id,
                height_px=capture_result.page_height_px,
                was_truncated=capture_result.was_truncated,
            )
            screenshot_id = screenshot_model.id

            # Etapa 3: Análise de compliance
            report = (
                await self._compliance_analyzer.analyze_compliance(
                    screenshot_path=(
                        capture_result.screenshot_path
                    ),
                    target_url=target_site.url,
                    screenshot_ref_id=screenshot_id,
                    cycle_id=cycle_id,
                    brand=target_site.brand,
                    target_site_id=target_site.id,
                )
            )

            logger.info(
                "Site processado com sucesso: url=%s, "
                "status=%s, regras_fail=%d",
                target_site.url,
                report.overall_status,
                sum(
                    1
                    for r in report.rule_results
                    if r.status == "FAIL"
                ),
            )

            return (
                SiteResult(
                    target_url=target_site.url,
                    success=True,
                    detections=[],
                ),
                report,
            )

        except Exception as exc:
            # Falha isolada — não interrompe ciclo
            logger.error(
                "Erro ao processar site %s: %s",
                target_site.url,
                str(exc),
                exc_info=True,
            )
            return (
                SiteResult(
                    target_url=target_site.url,
                    success=False,
                    detections=[],
                    error_message=str(exc),
                ),
                None,
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
        """Atualiza registro de MonitoringCycleModel com stats.

        Args:
            cycle_id: ID do ciclo a atualizar.
            ended_at: Timestamp de término do ciclo.
            sites_processed: Número de sites com sucesso.
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
