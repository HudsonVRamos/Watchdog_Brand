"""Notificador de email para relatórios de compliance.

Envia relatórios consolidados de compliance por email,
formatando o conteúdo conforme o status geral (compliant/non_compliant)
e incluindo detalhes por regra avaliada.

Implementa retry com intervalo configurável e isolamento de falhas
entre destinatários.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone

from brand_watchdog.alerts.alert_service import EmailProvider
from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import ComplianceReport

logger = logging.getLogger(__name__)


class ComplianceEmailNotifier:
    """Notificador de relatórios de compliance por email.

    Envia um email consolidado por ISP por ciclo de monitoramento,
    contendo o resultado de todas as regras avaliadas.

    Args:
        config: Configuração de alertas (sender, retries, etc.).
        email_provider: Provedor de email (SES ou SMTP).
    """

    def __init__(
        self,
        config: AlertConfig,
        email_provider: EmailProvider | None = None,
    ) -> None:
        self._config = config
        self._email_provider = email_provider

    async def send_cycle_report(
        self,
        reports: list[ComplianceReport],
        recipients: list[str],
    ) -> bool:
        """Envia relatório consolidado de ciclo com todos os sites.

        Envia um email curto com resumo no body e o relatório
        detalhado como arquivo .txt anexado.

        Args:
            reports: Lista de ComplianceReport de todos os sites.
            recipients: Lista de endereços de email.

        Returns:
            True se pelo menos um destinatário recebeu o email.
            False se nenhum destinatário recebeu.
        """
        if self._email_provider is None:
            logger.error(
                "Provedor de email não configurado."
            )
            return False

        if not recipients:
            logger.warning(
                "Nenhum destinatário configurado."
            )
            return False

        if not reports:
            logger.warning(
                "Nenhum relatório para enviar."
            )
            return False

        subject, body_summary, attachment_content = (
            self._format_cycle_email_with_attachment(reports)
        )

        # Enviar com anexo via raw email
        at_least_one_success = False
        for recipient in recipients:
            success = await self._send_with_attachment(
                recipient, subject, body_summary,
                attachment_content,
            )
            if success:
                at_least_one_success = True

        return at_least_one_success

    def _format_cycle_email_with_attachment(
        self, reports: list[ComplianceReport]
    ) -> tuple[str, str, str]:
        """Formata email com resumo curto + anexo detalhado.

        Returns:
            Tupla (subject, body_resumo, conteudo_anexo_txt).
        """
        from datetime import timezone

        non_compliant = [
            r for r in reports
            if r.overall_status == "non_compliant"
        ]
        compliant = [
            r for r in reports
            if r.overall_status == "compliant"
        ]

        total = len(reports)
        fail_count = len(non_compliant)

        if fail_count > 0:
            subject = (
                f"[Brand Watchdog] Ciclo concluido - "
                f"{fail_count}/{total} sites NON-COMPLIANT"
            )
        else:
            subject = (
                f"[Brand Watchdog] Ciclo concluido - "
                f"Todos os {total} sites COMPLIANT"
            )

        timestamp = reports[0].analyzed_at.astimezone(
            timezone.utc
        ).isoformat()

        # Body curto (resumo)
        # Contar sites com marca vs sem marca
        sites_with_brand_count = 0
        sites_no_brand_count = 0
        for report in compliant:
            na_count = sum(
                1 for r in report.rule_results
                if r.status == "NOT_APPLICABLE"
            )
            if na_count >= 4:
                sites_no_brand_count += 1
            else:
                sites_with_brand_count += 1

        body_summary = (
            f"Brand Watchdog - Relatorio de Compliance\n\n"
            f"Data: {timestamp}\n"
            f"Sites analisados: {total}\n"
            f"Nao-conformes: {fail_count}\n"
            f"Marca detectada e OK: {sites_with_brand_count}\n"
            f"Sem mencao a marca: {sites_no_brand_count}\n\n"
            f"Veja o relatorio detalhado no arquivo anexo."
        )

        # Conteúdo do anexo .txt (detalhado)
        att_parts: list[str] = []
        att_parts.append(
            "BRAND WATCHDOG - RELATORIO CONSOLIDADO DE COMPLIANCE"
        )
        att_parts.append("=" * 60)
        att_parts.append("")
        att_parts.append(f"Data do ciclo: {timestamp}")
        att_parts.append(
            f"Total de sites analisados: {total}"
        )
        att_parts.append(
            f"Sites em conformidade: {len(compliant)}"
        )
        att_parts.append(
            f"Sites em nao-conformidade: {fail_count}"
        )
        att_parts.append("")
        att_parts.append("")

        # Seção NON-COMPLIANT (detalhada)
        if non_compliant:
            att_parts.append(
                "-" * 60
            )
            att_parts.append(
                f"  SITES NAO-CONFORMES ({fail_count})"
            )
            att_parts.append(
                "-" * 60
            )
            att_parts.append("")

            for report in non_compliant:
                failed_rules = [
                    r for r in report.rule_results
                    if r.status == "FAIL"
                ]
                att_parts.append(
                    f"  URL: {report.target_url}"
                )
                att_parts.append(
                    f"  Status: NON_COMPLIANT"
                )
                att_parts.append(
                    f"  Regras violadas: {len(failed_rules)}"
                )
                att_parts.append("")
                for rule in failed_rules:
                    att_parts.append(
                        f"    [FAIL] {rule.rule_id} "
                        f"(confidence: {rule.confidence}%)"
                    )
                    att_parts.append(
                        f"           {rule.description}"
                    )
                    att_parts.append("")
                att_parts.append("")

        # Seção COMPLIANT (categorizada)
        if compliant:
            # Separar: sites com marca detectada vs sem menção
            sites_with_brand: list[ComplianceReport] = []
            sites_no_brand: list[ComplianceReport] = []

            for report in compliant:
                # Se maioria das regras é NOT_APPLICABLE, não
                # encontrou conteúdo da marca
                na_count = sum(
                    1 for r in report.rule_results
                    if r.status == "NOT_APPLICABLE"
                )
                if na_count >= 4:
                    sites_no_brand.append(report)
                else:
                    sites_with_brand.append(report)

            # Subcategoria: Marca detectada e em conformidade
            if sites_with_brand:
                att_parts.append("-" * 60)
                att_parts.append(
                    f"  MARCA DETECTADA - EM CONFORMIDADE "
                    f"({len(sites_with_brand)})"
                )
                att_parts.append("-" * 60)
                att_parts.append("")

                for report in sites_with_brand:
                    att_parts.append(
                        f"  URL: {report.target_url}"
                    )
                    att_parts.append(
                        f"  Status: COMPLIANT"
                    )
                    # Listar descrições das regras avaliadas
                    for rule in report.rule_results:
                        if rule.status == "PASS":
                            att_parts.append(
                                f"    [PASS] {rule.rule_id} "
                                f"({rule.confidence}%) - "
                                f"{rule.description[:150]}"
                            )
                        elif rule.status == "NOT_APPLICABLE":
                            att_parts.append(
                                f"    [N/A]  {rule.rule_id} - "
                                f"Nao aplicavel"
                            )
                    att_parts.append("")

            # Subcategoria: Nenhuma menção à marca encontrada
            if sites_no_brand:
                att_parts.append("-" * 60)
                att_parts.append(
                    f"  SEM MENCAO A MARCA "
                    f"({len(sites_no_brand)})"
                )
                att_parts.append(
                    "  (Nenhuma comunicacao da parceria "
                    "encontrada nestes sites)"
                )
                att_parts.append("-" * 60)
                att_parts.append("")

                for report in sites_no_brand:
                    att_parts.append(
                        f"  [--] {report.target_url}"
                    )
                    # Mostrar descrição do facilitator_role
                    # (geralmente diz "não encontrou menção")
                    for rule in report.rule_results:
                        if (rule.rule_id == "facilitator_role"
                                and rule.description):
                            att_parts.append(
                                f"       Obs: "
                                f"{rule.description[:150]}"
                            )
                            break
                att_parts.append("")

        # Rodapé
        att_parts.append("")
        att_parts.append("-" * 60)
        att_parts.append(
            "Relatorio automatico gerado pelo Brand Watchdog."
        )

        attachment_content = "\n".join(att_parts)
        return subject, body_summary, attachment_content

    async def _send_with_attachment(
        self,
        recipient: str,
        subject: str,
        body: str,
        attachment_content: str,
    ) -> bool:
        """Envia email com arquivo .txt anexado via raw MIME.

        Args:
            recipient: Endereço do destinatário.
            subject: Assunto do email.
            body: Corpo resumido do email.
            attachment_content: Conteúdo do arquivo .txt anexado.

        Returns:
            True se envio bem-sucedido, False se falhou.
        """
        import boto3
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication
        from datetime import datetime, timezone

        sender = self._config.ses_sender
        max_attempts = self._config.retry_attempts
        retry_interval = self._config.retry_interval_seconds

        # Construir email MIME com anexo
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        # Body do email
        body_part = MIMEText(body, "plain", "utf-8")
        msg.attach(body_part)

        # Anexo .txt
        now = datetime.now(timezone.utc)
        filename = (
            f"compliance_report_"
            f"{now.strftime('%Y%m%d_%H%M%S')}.txt"
        )
        attachment = MIMEApplication(
            attachment_content.encode("utf-8"),
            _subtype="txt",
        )
        attachment.add_header(
            "Content-Disposition",
            "attachment",
            filename=filename,
        )
        msg.attach(attachment)

        # Enviar via SES raw email com retry
        ses_client = boto3.client(
            "ses", region_name=self._config.ses_region
        )

        for attempt in range(1, max_attempts + 1):
            try:
                ses_client.send_raw_email(
                    Source=sender,
                    Destinations=[recipient],
                    RawMessage={"Data": msg.as_string()},
                )
                logger.info(
                    "Relatorio consolidado enviado com anexo: "
                    "destinatario=%s",
                    recipient,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "Falha ao enviar relatorio com anexo "
                    "(tentativa %d/%d): "
                    "destinatario=%s, erro=%s",
                    attempt,
                    max_attempts,
                    recipient,
                    str(exc),
                )
                if attempt < max_attempts:
                    await asyncio.sleep(retry_interval)

        logger.error(
            "Falha definitiva ao enviar relatorio "
            "consolidado: destinatario=%s",
            recipient,
        )
        return False

    async def send_compliance_report(
        self,
        report: ComplianceReport,
        recipients: list[str],
    ) -> bool:
        """Envia relatório de compliance para todos os destinatários.

        Formata o email com base no status do relatório e envia
        individualmente para cada destinatário. Se o envio falhar
        para um destinatário após retries, continua para os próximos.

        Args:
            report: Relatório de compliance a ser enviado.
            recipients: Lista de endereços de email.

        Returns:
            True se pelo menos um destinatário recebeu o email.
            False se nenhum destinatário recebeu.
        """
        if self._email_provider is None:
            logger.error(
                "Provedor de email não configurado. "
                "Não é possível enviar relatório de compliance."
            )
            return False

        if not recipients:
            logger.warning(
                "Nenhum destinatário configurado para "
                "relatório de compliance: target=%s",
                report.target_url,
            )
            return False

        subject, body = self._format_compliance_email(report)

        at_least_one_success = False
        for recipient in recipients:
            success = await self._send_with_retry(
                recipient, subject, body
            )
            if success:
                at_least_one_success = True

        return at_least_one_success

    def _format_compliance_email(
        self, report: ComplianceReport
    ) -> tuple[str, str]:
        """Formata subject e body do email de compliance.

        Para non_compliant: subject menciona não-conformidade,
        body lista regras com FAIL e seus detalhes.
        Para compliant: subject informacional, body confirma
        que todas as regras passaram.

        O body sempre contém: ISP URL, timestamp ISO 8601,
        overall status e tabela de todas as regras com status
        e confidence.

        Args:
            report: Relatório de compliance para formatar.

        Returns:
            Tupla (subject, body) do email.
        """
        # Timestamp ISO 8601 com timezone UTC
        timestamp_iso = report.analyzed_at.astimezone(
            timezone.utc
        ).isoformat()

        # Subject baseado no status
        if report.overall_status == "non_compliant":
            subject = (
                f"[Brand Watchdog] NON-COMPLIANT - "
                f"{report.target_url}"
            )
        else:
            subject = (
                f"[Brand Watchdog] Compliance Report - "
                f"{report.target_url}"
            )

        # Construir body
        body_parts: list[str] = []

        # Cabeçalho
        body_parts.append(
            "Brand Watchdog - Relatório de Compliance"
        )
        body_parts.append("=" * 50)
        body_parts.append("")

        # Informações gerais
        body_parts.append(f"ISP URL: {report.target_url}")
        body_parts.append(f"Timestamp: {timestamp_iso}")
        body_parts.append(
            f"Status Geral: {report.overall_status.upper()}"
        )
        body_parts.append("")

        # Seção de resumo por status
        if report.overall_status == "non_compliant":
            failed_rules = [
                r for r in report.rule_results
                if r.status == "FAIL"
            ]
            body_parts.append(
                f"ATENÇÃO: {len(failed_rules)} regra(s) "
                f"em não-conformidade."
            )
            body_parts.append("")
            body_parts.append("Regras com FALHA:")
            body_parts.append("-" * 40)
            for rule in failed_rules:
                body_parts.append(
                    f"  • {rule.rule_id}"
                )
                body_parts.append(
                    f"    Status: {rule.status}"
                )
                body_parts.append(
                    f"    Confidence: {rule.confidence}%"
                )
                body_parts.append(
                    f"    Descrição: {rule.description}"
                )
                body_parts.append("")
        else:
            body_parts.append(
                "[OK] Todas as regras de compliance foram "
                "aprovadas com sucesso."
            )
            body_parts.append("")

        # Tabela completa de todas as regras
        body_parts.append("Resultado Detalhado por Regra:")
        body_parts.append("-" * 40)
        for rule in report.rule_results:
            body_parts.append(
                f"  {rule.rule_id}: {rule.status} "
                f"(confidence: {rule.confidence}%)"
            )
        body_parts.append("")

        # Rodapé
        body_parts.append("---")
        body_parts.append(
            "Este é um relatório automático do Brand Watchdog."
        )

        body = "\n".join(body_parts)
        return subject, body

    async def _send_with_retry(
        self,
        recipient: str,
        subject: str,
        body: str,
    ) -> bool:
        """Envia email com retry configurável.

        Tenta enviar até retry_attempts vezes com intervalo
        de retry_interval_seconds entre tentativas.
        Se todos os retries falharem, loga o erro e retorna False.

        Args:
            recipient: Endereço do destinatário.
            subject: Assunto do email.
            body: Corpo do email.

        Returns:
            True se o envio foi bem-sucedido.
            False se todos os retries falharam.
        """
        sender = self._config.ses_sender
        max_attempts = self._config.retry_attempts
        retry_interval = self._config.retry_interval_seconds

        for attempt in range(1, max_attempts + 1):
            try:
                await self._email_provider.send(
                    recipient=recipient,
                    subject=subject,
                    body=body,
                    sender=sender,
                )
                logger.info(
                    "Relatório de compliance enviado: "
                    "destinatário=%s",
                    recipient,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "Falha ao enviar relatório "
                    "(tentativa %d/%d): "
                    "destinatário=%s, erro=%s",
                    attempt,
                    max_attempts,
                    recipient,
                    str(exc),
                )
                if attempt < max_attempts:
                    await asyncio.sleep(retry_interval)

        # Todos retries exauriram
        logger.error(
            "Falha definitiva ao enviar relatório de "
            "compliance após %d tentativas: "
            "destinatário=%s",
            max_attempts,
            recipient,
        )
        return False
