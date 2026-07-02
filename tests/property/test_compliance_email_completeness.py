"""Property tests para completude do email de compliance.

# Feature: mvp1-sky-amazon-compliance, Property 4: Email formatting completeness

Valida que para qualquer ComplianceReport válido (compliant ou non_compliant),
o email formatado contém: target URL, timestamp ISO 8601, overall status,
e para cada regra: rule_id, status e confidence.

**Validates: Requirements 8.2, 8.3, 8.4**
"""

from __future__ import annotations

from datetime import timezone

from hypothesis import given, settings, HealthCheck

from brand_watchdog.alerts.compliance_email_notifier import (
    ComplianceEmailNotifier,
)
from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import ComplianceReport

from tests.property.strategies import compliance_report


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _create_notifier() -> ComplianceEmailNotifier:
    """Cria instância mínima do ComplianceEmailNotifier para testes."""
    config = AlertConfig(
        ses_sender="test@example.com",
        recipients=["recipient@example.com"],
    )
    return ComplianceEmailNotifier(config=config)


class TestComplianceEmailFormattingCompleteness:
    """Property 4: Email formatting completeness.

    Para qualquer ComplianceReport válido (compliant ou non_compliant),
    o email formatado deve conter: target URL, timestamp ISO 8601,
    overall status, e para cada regra: rule_id, status e confidence.

    **Validates: Requirements 8.2, 8.3, 8.4**
    """

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_email_body_contains_target_url(
        self, report: ComplianceReport
    ) -> None:
        """Email body sempre contém a URL do ISP alvo."""
        notifier = _create_notifier()
        _subject, body = notifier._format_compliance_email(report)

        assert report.target_url in body, (
            f"target_url '{report.target_url}' não encontrada no body"
        )

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_email_body_contains_iso8601_timestamp(
        self, report: ComplianceReport
    ) -> None:
        """Email body sempre contém o timestamp de análise em ISO 8601."""
        notifier = _create_notifier()
        _subject, body = notifier._format_compliance_email(report)

        # O timestamp esperado é o analyzed_at convertido para UTC em ISO 8601
        expected_timestamp = report.analyzed_at.astimezone(
            timezone.utc
        ).isoformat()

        assert expected_timestamp in body, (
            f"Timestamp ISO 8601 '{expected_timestamp}' "
            f"não encontrado no body"
        )

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_email_body_contains_overall_status(
        self, report: ComplianceReport
    ) -> None:
        """Email body sempre contém o overall status em uppercase."""
        notifier = _create_notifier()
        _subject, body = notifier._format_compliance_email(report)

        expected_status = report.overall_status.upper()
        assert expected_status in body, (
            f"Overall status '{expected_status}' não encontrado no body"
        )

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_email_body_contains_all_rule_ids(
        self, report: ComplianceReport
    ) -> None:
        """Email body sempre contém o rule_id de cada regra avaliada."""
        notifier = _create_notifier()
        _subject, body = notifier._format_compliance_email(report)

        for rule_result in report.rule_results:
            assert rule_result.rule_id in body, (
                f"rule_id '{rule_result.rule_id}' não encontrado no body"
            )

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_email_body_contains_all_rule_statuses(
        self, report: ComplianceReport
    ) -> None:
        """Email body sempre contém o status de cada regra avaliada."""
        notifier = _create_notifier()
        _subject, body = notifier._format_compliance_email(report)

        for rule_result in report.rule_results:
            assert rule_result.status in body, (
                f"Status '{rule_result.status}' da regra "
                f"'{rule_result.rule_id}' não encontrado no body"
            )

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_email_body_contains_all_rule_confidences(
        self, report: ComplianceReport
    ) -> None:
        """Email body sempre contém a confidence de cada regra como 'XX%'."""
        notifier = _create_notifier()
        _subject, body = notifier._format_compliance_email(report)

        for rule_result in report.rule_results:
            confidence_str = f"{rule_result.confidence}%"
            assert confidence_str in body, (
                f"Confidence '{confidence_str}' da regra "
                f"'{rule_result.rule_id}' não encontrada no body"
            )

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_email_body_contains_all_required_fields(
        self, report: ComplianceReport
    ) -> None:
        """Email body contém TODOS os campos obrigatórios simultaneamente.

        Verificação consolidada:
        - target_url
        - timestamp ISO 8601
        - overall status (uppercase)
        - Para cada regra: rule_id, status, confidence (XX%)
        """
        notifier = _create_notifier()
        _subject, body = notifier._format_compliance_email(report)

        # 1. Target URL
        assert report.target_url in body

        # 2. Timestamp ISO 8601
        expected_timestamp = report.analyzed_at.astimezone(
            timezone.utc
        ).isoformat()
        assert expected_timestamp in body

        # 3. Overall status (uppercase)
        assert report.overall_status.upper() in body

        # 4. Cada regra: rule_id, status, confidence
        for rule_result in report.rule_results:
            assert rule_result.rule_id in body, (
                f"rule_id '{rule_result.rule_id}' ausente"
            )
            assert rule_result.status in body, (
                f"status '{rule_result.status}' da regra "
                f"'{rule_result.rule_id}' ausente"
            )
            confidence_str = f"{rule_result.confidence}%"
            assert confidence_str in body, (
                f"confidence '{confidence_str}' da regra "
                f"'{rule_result.rule_id}' ausente"
            )
