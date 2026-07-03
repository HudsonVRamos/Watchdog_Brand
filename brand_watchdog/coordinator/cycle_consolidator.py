"""Consolidador de resultados de ciclos de monitoramento.

Realiza polling periódico no banco de dados para verificar
quantos sites já completaram processamento dentro de um ciclo.
Quando todos completam ou o timeout é atingido, atualiza o
status do ciclo com os contadores finais e envia email
consolidado com relatório de compliance.

Requirements: 3.1, 3.2, 3.3
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from brand_watchdog.config import WorkerConfig
from brand_watchdog.models.database import get_session
from brand_watchdog.models.entities import (
    MonitoringCycleModel,
    SiteCycleResultModel,
    TargetSiteModel,
)

logger = logging.getLogger(__name__)


class CycleConsolidator:
    """Consolida resultados de um ciclo de monitoramento.

    Realiza polling periódico no banco para contar registros
    de SiteCycleResult associados a um cycle_id. Quando todos
    os sites estão completos ou o timeout de 60 minutos é
    atingido, atualiza o ciclo com status e contadores finais
    e envia email consolidado com relatório em anexo.

    Args:
        config: Configuração do Worker com intervalos de polling
            e timeout de consolidação.
        email_notifier: Notificador de email (opcional).
        recipients: Lista de destinatários para o email consolidado.
    """

    def __init__(
        self,
        config: WorkerConfig,
        email_notifier=None,
        recipients: list[str] | None = None,
    ) -> None:
        self._config = config
        self._poll_interval = (
            config.consolidation_poll_interval_seconds
        )
        self._timeout_minutes = (
            config.consolidation_timeout_minutes
        )
        self._email_notifier = email_notifier
        self._recipients = recipients or []

    async def consolidate(
        self, cycle_id: str, sites_dispatched: int
    ) -> str:
        """Realiza polling até todos os sites completarem ou timeout.

        Consulta o banco a cada poll_interval segundos contando
        registros de resultado para o cycle_id. Quando a contagem
        iguala sites_dispatched, calcula contadores e finaliza o
        ciclo como "completed". Se o timeout de 60 minutos for
        atingido, marca como "completed_with_timeout" e cria
        registros de falha para sites sem resultado.

        Args:
            cycle_id: ID do ciclo de monitoramento a consolidar.
            sites_dispatched: Número total de sites despachados
                na fila SQS para este ciclo.

        Returns:
            Status final do ciclo: "completed" ou
            "completed_with_timeout".
        """
        logger.info(
            "Iniciando consolidação do ciclo: id=%s, "
            "sites_dispatched=%d, poll_interval=%ds, "
            "timeout=%dmin",
            cycle_id,
            sites_dispatched,
            self._poll_interval,
            self._timeout_minutes,
        )

        start_time = datetime.now(timezone.utc)
        timeout_seconds = self._timeout_minutes * 60

        while True:
            # Conta resultados atuais
            result_count = await self._count_results(cycle_id)

            logger.debug(
                "Consolidação polling: cycle_id=%s, "
                "resultados=%d/%d",
                cycle_id,
                result_count,
                sites_dispatched,
            )

            # Verifica se todos os sites completaram
            if result_count >= sites_dispatched:
                status = await self._complete_cycle(cycle_id)
                logger.info(
                    "Ciclo consolidado com sucesso: id=%s, "
                    "status=%s",
                    cycle_id,
                    status,
                )
                # Enviar email consolidado do ciclo
                await self._send_consolidated_email(cycle_id)
                return status

            # Verifica timeout
            elapsed = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds()
            if elapsed >= timeout_seconds:
                status = await self._timeout_cycle(
                    cycle_id, sites_dispatched
                )
                logger.warning(
                    "Ciclo consolidado por timeout: id=%s, "
                    "status=%s, elapsed=%.0fs",
                    cycle_id,
                    status,
                    elapsed,
                )
                return status

            # Aguarda próximo poll
            await asyncio.sleep(self._poll_interval)

    async def _count_results(self, cycle_id: str) -> int:
        """Conta registros de SiteCycleResult para o ciclo.

        Args:
            cycle_id: ID do ciclo.

        Returns:
            Número total de resultados registrados.
        """
        async with get_session() as session:
            stmt = select(
                func.count(SiteCycleResultModel.id)
            ).where(
                SiteCycleResultModel.cycle_id == cycle_id
            )
            result = await session.execute(stmt)
            count = result.scalar() or 0
            return count

    async def _complete_cycle(self, cycle_id: str) -> str:
        """Finaliza ciclo como 'completed' com contadores.

        Calcula sites_processed, sites_failed e detections_found
        a partir dos registros de SiteCycleResult e atualiza o
        MonitoringCycleModel.

        Args:
            cycle_id: ID do ciclo a finalizar.

        Returns:
            Status final: "completed".
        """
        async with get_session() as session:
            # Conta sites com sucesso
            stmt_success = select(
                func.count(SiteCycleResultModel.id)
            ).where(
                SiteCycleResultModel.cycle_id == cycle_id,
                SiteCycleResultModel.status
                == SiteCycleResultModel.STATUS_SUCCESS,
            )
            result = await session.execute(stmt_success)
            sites_processed = result.scalar() or 0

            # Conta sites com falha
            stmt_failed = select(
                func.count(SiteCycleResultModel.id)
            ).where(
                SiteCycleResultModel.cycle_id == cycle_id,
                SiteCycleResultModel.status
                == SiteCycleResultModel.STATUS_FAILURE,
            )
            result = await session.execute(stmt_failed)
            sites_failed = result.scalar() or 0

            # Soma detecções
            stmt_detections = select(
                func.coalesce(
                    func.sum(
                        SiteCycleResultModel.detections_count
                    ),
                    0,
                )
            ).where(
                SiteCycleResultModel.cycle_id == cycle_id
            )
            result = await session.execute(stmt_detections)
            detections_found = result.scalar() or 0

            # Atualiza o ciclo
            stmt_cycle = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_id
            )
            result = await session.execute(stmt_cycle)
            cycle_model = result.scalar_one_or_none()

            if cycle_model is not None:
                cycle_model.status = (
                    MonitoringCycleModel.STATUS_COMPLETED
                )
                cycle_model.sites_processed = sites_processed
                cycle_model.sites_failed = sites_failed
                cycle_model.detections_found = detections_found
                cycle_model.ended_at = datetime.now(timezone.utc)

        logger.info(
            "Ciclo atualizado: id=%s, status=completed, "
            "processed=%d, failed=%d, detections=%d",
            cycle_id,
            sites_processed,
            sites_failed,
            detections_found,
        )

        return MonitoringCycleModel.STATUS_COMPLETED

    async def _timeout_cycle(
        self, cycle_id: str, sites_dispatched: int
    ) -> str:
        """Finaliza ciclo como 'completed_with_timeout'.

        Identifica sites sem resultado, cria registros de falha
        para eles, e atualiza o ciclo com contadores e status
        de timeout.

        Args:
            cycle_id: ID do ciclo.
            sites_dispatched: Total de sites despachados.

        Returns:
            Status final: "completed_with_timeout".
        """
        async with get_session() as session:
            # Encontra sites SEM resultado para este ciclo
            # Busca todos os sites ativos que foram despachados
            # mas não possuem resultado
            subquery = (
                select(SiteCycleResultModel.site_id)
                .where(
                    SiteCycleResultModel.cycle_id == cycle_id
                )
                .subquery()
            )

            stmt_missing = select(TargetSiteModel.id).where(
                TargetSiteModel.active.is_(True),
                TargetSiteModel.id.notin_(
                    select(subquery.c.site_id)
                ),
            )
            result = await session.execute(stmt_missing)
            missing_site_ids = [
                row[0] for row in result.fetchall()
            ]

            # Cria registros de falha para sites sem resultado
            now = datetime.now(timezone.utc)
            for site_id in missing_site_ids:
                failure_result = SiteCycleResultModel(
                    id=str(uuid.uuid4()),
                    site_id=site_id,
                    cycle_id=cycle_id,
                    status=SiteCycleResultModel.STATUS_FAILURE,
                    detections_count=0,
                    failure_reason=(
                        "Timeout de consolidação (60min)"
                    ),
                    completed_at=now,
                )
                session.add(failure_result)

            await session.flush()

            # Agora calcula contadores finais (incluindo as
            # falhas recém-criadas)
            stmt_success = select(
                func.count(SiteCycleResultModel.id)
            ).where(
                SiteCycleResultModel.cycle_id == cycle_id,
                SiteCycleResultModel.status
                == SiteCycleResultModel.STATUS_SUCCESS,
            )
            result = await session.execute(stmt_success)
            sites_processed = result.scalar() or 0

            stmt_failed = select(
                func.count(SiteCycleResultModel.id)
            ).where(
                SiteCycleResultModel.cycle_id == cycle_id,
                SiteCycleResultModel.status
                == SiteCycleResultModel.STATUS_FAILURE,
            )
            result = await session.execute(stmt_failed)
            sites_failed = result.scalar() or 0

            stmt_detections = select(
                func.coalesce(
                    func.sum(
                        SiteCycleResultModel.detections_count
                    ),
                    0,
                )
            ).where(
                SiteCycleResultModel.cycle_id == cycle_id
            )
            result = await session.execute(stmt_detections)
            detections_found = result.scalar() or 0

            # Atualiza o ciclo
            stmt_cycle = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_id
            )
            result = await session.execute(stmt_cycle)
            cycle_model = result.scalar_one_or_none()

            if cycle_model is not None:
                cycle_model.status = (
                    MonitoringCycleModel.STATUS_COMPLETED_WITH_TIMEOUT
                )
                cycle_model.sites_processed = sites_processed
                cycle_model.sites_failed = sites_failed
                cycle_model.detections_found = detections_found
                cycle_model.ended_at = now

        logger.warning(
            "Ciclo timeout: id=%s, "
            "status=completed_with_timeout, "
            "processed=%d, failed=%d (timeout=%d), "
            "detections=%d",
            cycle_id,
            sites_processed,
            sites_failed,
            len(missing_site_ids),
            detections_found,
        )

        return MonitoringCycleModel.STATUS_COMPLETED_WITH_TIMEOUT

    async def _send_consolidated_email(
        self, cycle_id: str
    ) -> None:
        """Envia email consolidado com relatório de todos os sites do ciclo.

        Busca os ComplianceReports de todos os sites processados no ciclo,
        incluindo TODAS as 6 regras (PASS e FAIL) para cada site.
        Envia um único email com relatório em arquivo anexo.

        Args:
            cycle_id: ID do ciclo consolidado.
        """
        if self._email_notifier is None:
            logger.debug(
                "Email notifier não configurado, pulando envio"
            )
            return

        if not self._recipients:
            logger.warning(
                "Nenhum destinatário configurado para email"
            )
            return

        try:
            from brand_watchdog.models.dataclasses import (
                COMPLIANCE_RULES,
                ComplianceReport,
                ComplianceRuleResult,
            )
            from brand_watchdog.models.entities import (
                DetectionResultModel,
            )

            reports: list[ComplianceReport] = []

            async with get_session() as session:
                # Busca TODOS os resultados para este ciclo (success + failure)
                stmt = (
                    select(SiteCycleResultModel)
                    .where(
                        SiteCycleResultModel.cycle_id == cycle_id,
                    )
                )
                result = await session.execute(stmt)
                site_results = result.scalars().all()

                for site_result in site_results:
                    # Busca o target_site para obter a URL
                    site_stmt = select(TargetSiteModel).where(
                        TargetSiteModel.id == site_result.site_id
                    )
                    site_row = await session.execute(site_stmt)
                    target_site = site_row.scalar_one_or_none()

                    if target_site is None:
                        continue

                    # Se o site falhou no processamento, reportar como erro
                    if site_result.status == "failure":
                        rule_results = [
                            ComplianceRuleResult(
                                rule_id="processing_error",
                                status="FAIL",
                                confidence=0,
                                description=(
                                    site_result.failure_reason
                                    or "Erro no processamento"
                                ),
                            )
                        ]
                        report = ComplianceReport(
                            target_url=target_site.url,
                            analyzed_at=site_result.completed_at,
                            overall_status="error",
                            rule_results=rule_results,
                            screenshot_ref_id="",
                            cycle_id=cycle_id,
                        )
                        reports.append(report)
                        continue

                    # Busca detection_results (violações FAIL) para este site/ciclo
                    det_stmt = select(DetectionResultModel).where(
                        DetectionResultModel.target_site_id == site_result.site_id,
                        DetectionResultModel.monitoring_cycle_id == cycle_id,
                    )
                    det_result = await session.execute(det_stmt)
                    detections = det_result.scalars().all()

                    # Monta mapa de regras FAIL (rule_id -> detection)
                    fail_map = {
                        d.match_type: d for d in detections
                    }

                    # Constrói TODAS as 6 regras (PASS ou FAIL)
                    rule_results = []
                    for rule_id in COMPLIANCE_RULES:
                        if rule_id in fail_map:
                            d = fail_map[rule_id]
                            rule_results.append(
                                ComplianceRuleResult(
                                    rule_id=rule_id,
                                    status="FAIL",
                                    confidence=d.confidence or 0,
                                    description=d.description or "",
                                )
                            )
                        else:
                            rule_results.append(
                                ComplianceRuleResult(
                                    rule_id=rule_id,
                                    status="PASS",
                                    confidence=100,
                                    description="Em conformidade.",
                                )
                            )

                    overall_status = (
                        "non_compliant" if fail_map else "compliant"
                    )

                    report = ComplianceReport(
                        target_url=target_site.url,
                        analyzed_at=site_result.completed_at,
                        overall_status=overall_status,
                        rule_results=rule_results,
                        screenshot_ref_id="",
                        cycle_id=cycle_id,
                    )
                    reports.append(report)

            if not reports:
                logger.info(
                    "Nenhum relatório para enviar no ciclo %s",
                    cycle_id,
                )
                return

            # Envia email consolidado (1 email, relatório em arquivo)
            success = await self._email_notifier.send_cycle_report(
                reports=reports,
                recipients=self._recipients,
            )

            if success:
                logger.info(
                    "Email consolidado enviado: cycle_id=%s, "
                    "sites=%d, recipients=%d",
                    cycle_id,
                    len(reports),
                    len(self._recipients),
                )
            else:
                logger.warning(
                    "Falha ao enviar email consolidado: "
                    "cycle_id=%s",
                    cycle_id,
                )

        except Exception as exc:
            logger.error(
                "Erro ao enviar email consolidado: "
                "cycle_id=%s, erro=%s",
                cycle_id,
                str(exc),
            )
