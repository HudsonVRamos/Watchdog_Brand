"""Testes unitários para o ComplianceEmailNotifier.

Valida:
- Formatação de email para relatórios compliant e non_compliant
- Envio com retry e isolamento de falhas entre destinatários
- Retorno correto baseado em sucesso/falha de envio
- Conteúdo obrigatório no email (URL, timestamp, status, regras)
- Retry logic: falha nas primeiras N-1 tentativas, sucesso na N-ésima
- Exatamente 1 email por ISP por ciclo por destinatário

Validates: Requirements 8.1, 8.2, 8.3, 8.5, 8.6
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from brand_watchdog.alerts.alert_service import EmailProvider
from brand_watchdog.alerts.compliance_email_notifier import (
    ComplianceEmailNotifier,
)
from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import (
    ComplianceReport,
    ComplianceRuleResult,
)


# --- Fixtures e Helpers ---


class FakeEmailProvider(EmailProvider):
    """Provedor de email fake para testes."""

    def __init__(self, should_fail: bool = False) -> None:
        self.sent_emails: list[dict] = []
        self.should_fail = should_fail
        self.call_count = 0

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        sender: str,
    ) -> None:
        self.call_count += 1
        if self.should_fail:
            raise ConnectionError("Falha simulada no envio")
        self.sent_emails.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "sender": sender,
            }
        )


def _make_config(
    retry_attempts: int = 3,
    retry_interval_seconds: int = 0,
) -> AlertConfig:
    """Cria AlertConfig para testes (intervalo 0 para rapidez)."""
    return AlertConfig(
        provider="ses",
        ses_region="us-east-1",
        ses_sender="noreply@example.com",
        recipients=["test@example.com"],
        retry_attempts=retry_attempts,
        retry_interval_seconds=retry_interval_seconds,
    )


def _make_compliant_report() -> ComplianceReport:
    """Cria um ComplianceReport com status compliant."""
    return ComplianceReport(
        target_url="https://isp-example.com/promo",
        analyzed_at=datetime(
            2024, 7, 10, 14, 30, 0, tzinfo=timezone.utc
        ),
        overall_status="compliant",
        rule_results=[
            ComplianceRuleResult(
                rule_id="facilitator_role",
                status="PASS",
                confidence=95,
                description="SKY+ referenciado corretamente.",
            ),
            ComplianceRuleResult(
                rule_id="logo_application",
                status="PASS",
                confidence=90,
                description="Logos na ordem correta.",
            ),
            ComplianceRuleResult(
                rule_id="logo_effects",
                status="PASS",
                confidence=88,
                description="Nenhum efeito visual detectado.",
            ),
            ComplianceRuleResult(
                rule_id="content_separation",
                status="PASS",
                confidence=92,
                description="Conteúdo parceiro separado.",
            ),
            ComplianceRuleResult(
                rule_id="naming_pricing",
                status="PASS",
                confidence=97,
                description="Nome e preço corretos.",
            ),
            ComplianceRuleResult(
                rule_id="kv_integrity",
                status="PASS",
                confidence=91,
                description="KV sem alterações detectadas.",
            ),
        ],
        screenshot_ref_id="ref-001",
        cycle_id="cycle-001",
    )


def _make_non_compliant_report() -> ComplianceReport:
    """Cria um ComplianceReport com status non_compliant."""
    return ComplianceReport(
        target_url="https://isp-violator.com/sky",
        analyzed_at=datetime(
            2024, 7, 10, 16, 0, 0, tzinfo=timezone.utc
        ),
        overall_status="non_compliant",
        rule_results=[
            ComplianceRuleResult(
                rule_id="facilitator_role",
                status="FAIL",
                confidence=87,
                description=(
                    "Menção a Amazon Prime sem referência SKY+."
                ),
            ),
            ComplianceRuleResult(
                rule_id="logo_application",
                status="PASS",
                confidence=90,
                description="Logos na ordem correta.",
            ),
            ComplianceRuleResult(
                rule_id="naming_pricing",
                status="FAIL",
                confidence=82,
                description="Termo 'grátis' usado no contexto.",
            ),
        ],
        screenshot_ref_id="ref-002",
        cycle_id="cycle-002",
    )


# --- Testes de Formatação ---


class TestFormatComplianceEmail:
    """Testes de formatação do email de compliance."""

    def test_compliant_subject_is_informational(self) -> None:
        """Subject para compliant não menciona non-compliance."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_compliant_report()

        subject, _ = notifier._format_compliance_email(report)

        assert "NON-COMPLIANT" not in subject
        assert "Compliance Report" in subject
        assert report.target_url in subject

    def test_non_compliant_subject_mentions_non_compliance(
        self,
    ) -> None:
        """Subject para non_compliant menciona NON-COMPLIANT."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_non_compliant_report()

        subject, _ = notifier._format_compliance_email(report)

        assert "NON-COMPLIANT" in subject
        assert report.target_url in subject

    def test_body_contains_isp_url(self) -> None:
        """Body sempre contém a ISP URL."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_compliant_report()

        _, body = notifier._format_compliance_email(report)

        assert report.target_url in body

    def test_body_contains_iso_8601_timestamp(self) -> None:
        """Body contém timestamp em formato ISO 8601."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_compliant_report()

        _, body = notifier._format_compliance_email(report)

        # Timestamp ISO 8601 UTC
        expected_ts = report.analyzed_at.astimezone(
            timezone.utc
        ).isoformat()
        assert expected_ts in body

    def test_body_contains_overall_status(self) -> None:
        """Body contém o overall status."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_non_compliant_report()

        _, body = notifier._format_compliance_email(report)

        assert "NON_COMPLIANT" in body

    def test_body_contains_all_rules(self) -> None:
        """Body contém todas as regras com status e confidence."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_compliant_report()

        _, body = notifier._format_compliance_email(report)

        for rule in report.rule_results:
            assert rule.rule_id in body
            assert rule.status in body
            assert f"{rule.confidence}%" in body

    def test_non_compliant_body_lists_failed_rules(self) -> None:
        """Body de non_compliant lista regras com FAIL e detalhes."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_non_compliant_report()

        _, body = notifier._format_compliance_email(report)

        failed = [
            r for r in report.rule_results if r.status == "FAIL"
        ]
        for rule in failed:
            assert rule.rule_id in body
            assert rule.description in body
            assert f"{rule.confidence}%" in body

    def test_compliant_body_confirms_all_passed(self) -> None:
        """Body de compliant confirma que todas as regras passaram."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config)
        report = _make_compliant_report()

        _, body = notifier._format_compliance_email(report)

        assert "aprovadas" in body.lower() or "passaram" in body.lower()


# --- Testes de Envio ---


class TestSendComplianceReport:
    """Testes de envio de relatórios de compliance."""

    @pytest.mark.asyncio
    async def test_send_to_single_recipient_success(
        self,
    ) -> None:
        """Envia com sucesso para um destinatário."""
        config = _make_config()
        provider = FakeEmailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        result = await notifier.send_compliance_report(
            report, ["user@example.com"]
        )

        assert result is True
        assert len(provider.sent_emails) == 1
        assert (
            provider.sent_emails[0]["recipient"]
            == "user@example.com"
        )

    @pytest.mark.asyncio
    async def test_send_to_multiple_recipients(self) -> None:
        """Envia para múltiplos destinatários."""
        config = _make_config()
        provider = FakeEmailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()
        recipients = [
            "a@example.com",
            "b@example.com",
            "c@example.com",
        ]

        result = await notifier.send_compliance_report(
            report, recipients
        )

        assert result is True
        assert len(provider.sent_emails) == 3

    @pytest.mark.asyncio
    async def test_returns_false_when_no_provider(self) -> None:
        """Retorna False se nenhum provider configurado."""
        config = _make_config()
        notifier = ComplianceEmailNotifier(config, None)
        report = _make_compliant_report()

        result = await notifier.send_compliance_report(
            report, ["user@example.com"]
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_recipients(
        self,
    ) -> None:
        """Retorna False se lista de destinatários está vazia."""
        config = _make_config()
        provider = FakeEmailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        result = await notifier.send_compliance_report(
            report, []
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_if_at_least_one_success(
        self,
    ) -> None:
        """Retorna True se pelo menos um destinatário recebeu."""
        config = _make_config()
        call_count = 0

        class PartialFailProvider(EmailProvider):
            """Provider que falha no primeiro destinatário."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                nonlocal call_count
                call_count += 1
                if recipient == "fail@example.com":
                    raise ConnectionError("Falha")

        provider = PartialFailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        result = await notifier.send_compliance_report(
            report,
            ["fail@example.com", "ok@example.com"],
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_all_fail(self) -> None:
        """Retorna False se todos os destinatários falharam."""
        config = _make_config()
        provider = FakeEmailProvider(should_fail=True)
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        result = await notifier.send_compliance_report(
            report, ["user@example.com"]
        )

        assert result is False


# --- Testes de Retry ---


class TestSendWithRetry:
    """Testes do mecanismo de retry."""

    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        """Tenta retry_attempts vezes antes de desistir."""
        config = _make_config(
            retry_attempts=3, retry_interval_seconds=0
        )
        provider = FakeEmailProvider(should_fail=True)
        notifier = ComplianceEmailNotifier(config, provider)

        result = await notifier._send_with_retry(
            "user@example.com", "Subject", "Body"
        )

        assert result is False
        assert provider.call_count == 3

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt(self) -> None:
        """Sucesso na segunda tentativa retorna True."""
        config = _make_config(
            retry_attempts=3, retry_interval_seconds=0
        )
        attempt = 0

        class RetryProvider(EmailProvider):
            """Provider que falha na primeira tentativa."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                nonlocal attempt
                attempt += 1
                if attempt == 1:
                    raise ConnectionError("Falha temporária")

        provider = RetryProvider()
        notifier = ComplianceEmailNotifier(config, provider)

        result = await notifier._send_with_retry(
            "user@example.com", "Subject", "Body"
        )

        assert result is True
        assert attempt == 2

    @pytest.mark.asyncio
    async def test_continues_to_next_recipient_on_failure(
        self,
    ) -> None:
        """Falha para um destinatário não impede envio aos próximos."""
        config = _make_config(
            retry_attempts=2, retry_interval_seconds=0
        )
        sent_to: list[str] = []

        class SelectiveFailProvider(EmailProvider):
            """Provider que falha apenas para destinatários específicos."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                if recipient == "bad@example.com":
                    raise ConnectionError("Falha")
                sent_to.append(recipient)

        provider = SelectiveFailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        result = await notifier.send_compliance_report(
            report,
            [
                "bad@example.com",
                "good1@example.com",
                "good2@example.com",
            ],
        )

        assert result is True
        assert "good1@example.com" in sent_to
        assert "good2@example.com" in sent_to


# --- Testes de Retry com Falha N-1 vezes ---


class TestRetryLogicNMinus1Failures:
    """Testes de retry onde o provider falha N-1 vezes e sucede na N-ésima.

    Validates: Requirements 8.5
    """

    @pytest.mark.asyncio
    async def test_succeeds_on_last_attempt(self) -> None:
        """Provider falha nas primeiras N-1 tentativas, sucede na N-ésima.

        Com retry_attempts=3, falha nas tentativas 1 e 2, sucede na 3.
        Verifica que o total de chamadas é exatamente retry_attempts.
        """
        max_attempts = 3
        config = _make_config(
            retry_attempts=max_attempts,
            retry_interval_seconds=0,
        )
        attempt_count = 0

        class FailUntilLastProvider(EmailProvider):
            """Provider que falha até a última tentativa."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                nonlocal attempt_count
                attempt_count += 1
                if attempt_count < max_attempts:
                    raise ConnectionError(
                        f"Falha na tentativa {attempt_count}"
                    )

        provider = FailUntilLastProvider()
        notifier = ComplianceEmailNotifier(config, provider)

        result = await notifier._send_with_retry(
            "user@example.com", "Subject", "Body"
        )

        assert result is True
        assert attempt_count == max_attempts

    @pytest.mark.asyncio
    async def test_retry_5_attempts_succeeds_on_fifth(self) -> None:
        """Com retry_attempts=5, falha 4 vezes, sucede na 5a tentativa."""
        max_attempts = 5
        config = _make_config(
            retry_attempts=max_attempts,
            retry_interval_seconds=0,
        )
        attempt_count = 0

        class FailUntilLastProvider(EmailProvider):
            """Provider que falha até a última tentativa."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                nonlocal attempt_count
                attempt_count += 1
                if attempt_count < max_attempts:
                    raise ConnectionError(
                        f"Falha na tentativa {attempt_count}"
                    )

        provider = FailUntilLastProvider()
        notifier = ComplianceEmailNotifier(config, provider)

        result = await notifier._send_with_retry(
            "user@example.com", "Subject", "Body"
        )

        assert result is True
        assert attempt_count == max_attempts

    @pytest.mark.asyncio
    async def test_all_attempts_exhausted_returns_false(self) -> None:
        """Com retry_attempts=3, falha todas 3 vezes e retorna False.

        Verifica que o total de chamadas é exatamente retry_attempts.
        """
        max_attempts = 3
        config = _make_config(
            retry_attempts=max_attempts,
            retry_interval_seconds=0,
        )
        attempt_count = 0

        class AlwaysFailProvider(EmailProvider):
            """Provider que sempre falha."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                nonlocal attempt_count
                attempt_count += 1
                raise ConnectionError(
                    f"Falha persistente na tentativa {attempt_count}"
                )

        provider = AlwaysFailProvider()
        notifier = ComplianceEmailNotifier(config, provider)

        result = await notifier._send_with_retry(
            "user@example.com", "Subject", "Body"
        )

        assert result is False
        assert attempt_count == max_attempts


# --- Testes de Isolamento entre Recipients ---


class TestRecipientIsolation:
    """Testes de isolamento: falha em um destinatário não bloqueia outros.

    Validates: Requirements 8.6
    """

    @pytest.mark.asyncio
    async def test_failure_for_first_does_not_block_second(
        self,
    ) -> None:
        """Falha para o primeiro recipient permite envio ao segundo."""
        config = _make_config(
            retry_attempts=2, retry_interval_seconds=0
        )
        successful_recipients: list[str] = []

        class FirstFailsProvider(EmailProvider):
            """Provider que falha apenas para o primeiro recipient."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                if recipient == "first@example.com":
                    raise ConnectionError("Falha permanente")
                successful_recipients.append(recipient)

        provider = FirstFailsProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        result = await notifier.send_compliance_report(
            report,
            ["first@example.com", "second@example.com"],
        )

        # Pelo menos um sucedeu
        assert result is True
        # Segundo recebeu normalmente
        assert "second@example.com" in successful_recipients
        # Primeiro não consta nos sucedidos
        assert "first@example.com" not in successful_recipients

    @pytest.mark.asyncio
    async def test_middle_failure_does_not_affect_others(
        self,
    ) -> None:
        """Falha no meio da lista não impede envio aos demais."""
        config = _make_config(
            retry_attempts=1, retry_interval_seconds=0
        )
        successful_recipients: list[str] = []

        class MiddleFailsProvider(EmailProvider):
            """Provider que falha para o destinatário do meio."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                if recipient == "middle@example.com":
                    raise ConnectionError("Falha no meio")
                successful_recipients.append(recipient)

        provider = MiddleFailsProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_non_compliant_report()

        result = await notifier.send_compliance_report(
            report,
            [
                "first@example.com",
                "middle@example.com",
                "last@example.com",
            ],
        )

        assert result is True
        assert "first@example.com" in successful_recipients
        assert "last@example.com" in successful_recipients
        assert "middle@example.com" not in successful_recipients


# --- Testes: Exatamente 1 email por ISP por ciclo ---


class TestOneEmailPerIspPerCycle:
    """Testes que verificam exatamente 1 email enviado por ISP por ciclo.

    Validates: Requirements 8.1
    """

    @pytest.mark.asyncio
    async def test_one_report_sends_exactly_one_email_per_recipient(
        self,
    ) -> None:
        """Chamar send_compliance_report com 1 report envia 1 email por recipient."""
        config = _make_config()
        provider = FakeEmailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()
        recipients = ["a@example.com", "b@example.com"]

        await notifier.send_compliance_report(report, recipients)

        # Exatamente 1 email por destinatário
        assert len(provider.sent_emails) == 2
        recipients_sent = [
            e["recipient"] for e in provider.sent_emails
        ]
        assert recipients_sent.count("a@example.com") == 1
        assert recipients_sent.count("b@example.com") == 1

    @pytest.mark.asyncio
    async def test_single_recipient_gets_exactly_one_email(
        self,
    ) -> None:
        """Com 1 único recipient, exatamente 1 email é enviado."""
        config = _make_config()
        provider = FakeEmailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_non_compliant_report()

        await notifier.send_compliance_report(
            report, ["solo@example.com"]
        )

        assert len(provider.sent_emails) == 1
        assert (
            provider.sent_emails[0]["recipient"]
            == "solo@example.com"
        )

    @pytest.mark.asyncio
    async def test_email_subject_contains_target_url_for_isp_identification(
        self,
    ) -> None:
        """O email enviado identifica o ISP pela URL no subject."""
        config = _make_config()
        provider = FakeEmailProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        await notifier.send_compliance_report(
            report, ["user@example.com"]
        )

        subject = provider.sent_emails[0]["subject"]
        assert report.target_url in subject

    @pytest.mark.asyncio
    async def test_no_duplicate_emails_on_retry_success(
        self,
    ) -> None:
        """Retry bem-sucedido não gera emails duplicados.

        Se o provider falha na primeira tentativa mas sucede na segunda,
        apenas 1 email é efetivamente recebido pelo destinatário.
        """
        config = _make_config(
            retry_attempts=3, retry_interval_seconds=0
        )
        delivered_emails: list[dict] = []
        attempt_count = 0

        class RetryOnceProvider(EmailProvider):
            """Provider que falha uma vez, depois sucede."""

            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                nonlocal attempt_count
                attempt_count += 1
                if attempt_count == 1:
                    raise ConnectionError("Falha temporária")
                delivered_emails.append(
                    {"recipient": recipient, "subject": subject}
                )

        provider = RetryOnceProvider()
        notifier = ComplianceEmailNotifier(config, provider)
        report = _make_compliant_report()

        await notifier.send_compliance_report(
            report, ["user@example.com"]
        )

        # Apenas 1 email entregue (sem duplicatas)
        assert len(delivered_emails) == 1
        assert (
            delivered_emails[0]["recipient"] == "user@example.com"
        )
