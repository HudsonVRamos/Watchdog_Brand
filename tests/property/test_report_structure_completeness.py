"""Property test para completude da estrutura do ComplianceReport.

# Feature: mvp1-sky-amazon-compliance, Property 9: Report structure completeness

**Validates: Requirements 7.1, 7.2**

Para qualquer resposta Bedrock válida contendo resultados para todas as regras
configuradas, o ComplianceReport parseado deve conter exatamente um
ComplianceRuleResult por regra configurada (6 regras total), cada um com:
rule_id válido, status em {"PASS", "FAIL", "NOT_APPLICABLE"}, confidence
inteiro em [0, 100], e description não-vazia com ≤ 1024 caracteres.
"""

from __future__ import annotations

from hypothesis import given, settings

from brand_watchdog.analyzer.compliance_report_parser import (
    ComplianceReportParser,
)
from brand_watchdog.models.dataclasses import COMPLIANCE_RULES

from .strategies import valid_bedrock_compliance_response


_PBT_SETTINGS = settings(max_examples=30)

_VALID_STATUSES = {"PASS", "FAIL", "NOT_APPLICABLE"}


class TestReportStructureCompleteness:
    """Property 9: Report structure completeness.

    **Validates: Requirements 7.1, 7.2**
    """

    @_PBT_SETTINGS
    @given(response=valid_bedrock_compliance_response())
    def test_parsed_report_contains_exactly_6_rule_results(
        self, response: dict
    ) -> None:
        """O report parseado deve conter exatamente 6 ComplianceRuleResult."""
        parser = ComplianceReportParser()
        report = parser.parse_response(
            raw_json=response,
            target_url="https://example.com/test",
            screenshot_ref_id="ref-test-123",
            cycle_id="cycle-test-456",
        )

        assert len(report.rule_results) == 6, (
            f"Esperado 6 rule_results, obtido {len(report.rule_results)}"
        )

    @_PBT_SETTINGS
    @given(response=valid_bedrock_compliance_response())
    def test_each_rule_id_matches_configured_rules(
        self, response: dict
    ) -> None:
        """Cada rule_id no report deve ser um dos COMPLIANCE_RULES."""
        parser = ComplianceReportParser()
        report = parser.parse_response(
            raw_json=response,
            target_url="https://example.com/test",
            screenshot_ref_id="ref-test-123",
            cycle_id="cycle-test-456",
        )

        rule_ids = {r.rule_id for r in report.rule_results}
        expected_rule_ids = set(COMPLIANCE_RULES)

        assert rule_ids == expected_rule_ids, (
            f"rule_ids divergem. Esperado: {expected_rule_ids}, "
            f"Obtido: {rule_ids}"
        )

    @_PBT_SETTINGS
    @given(response=valid_bedrock_compliance_response())
    def test_each_status_is_valid(
        self, response: dict
    ) -> None:
        """Cada status deve ser PASS, FAIL, ou NOT_APPLICABLE."""
        parser = ComplianceReportParser()
        report = parser.parse_response(
            raw_json=response,
            target_url="https://example.com/test",
            screenshot_ref_id="ref-test-123",
            cycle_id="cycle-test-456",
        )

        for rule_result in report.rule_results:
            assert rule_result.status in _VALID_STATUSES, (
                f"rule_id '{rule_result.rule_id}' tem status inválido: "
                f"'{rule_result.status}'. Esperado: {_VALID_STATUSES}"
            )

    @_PBT_SETTINGS
    @given(response=valid_bedrock_compliance_response())
    def test_each_confidence_in_range_0_100(
        self, response: dict
    ) -> None:
        """Cada confidence deve ser inteiro entre 0 e 100."""
        parser = ComplianceReportParser()
        report = parser.parse_response(
            raw_json=response,
            target_url="https://example.com/test",
            screenshot_ref_id="ref-test-123",
            cycle_id="cycle-test-456",
        )

        for rule_result in report.rule_results:
            assert isinstance(rule_result.confidence, int), (
                f"rule_id '{rule_result.rule_id}': confidence "
                f"não é int: {type(rule_result.confidence)}"
            )
            assert 0 <= rule_result.confidence <= 100, (
                f"rule_id '{rule_result.rule_id}': confidence "
                f"{rule_result.confidence} fora de [0, 100]"
            )

    @_PBT_SETTINGS
    @given(response=valid_bedrock_compliance_response())
    def test_each_description_non_empty_and_within_limit(
        self, response: dict
    ) -> None:
        """Cada description deve ser não-vazia e ter ≤ 1024 caracteres."""
        parser = ComplianceReportParser()
        report = parser.parse_response(
            raw_json=response,
            target_url="https://example.com/test",
            screenshot_ref_id="ref-test-123",
            cycle_id="cycle-test-456",
        )

        for rule_result in report.rule_results:
            assert len(rule_result.description) > 0, (
                f"rule_id '{rule_result.rule_id}': description vazia"
            )
            assert len(rule_result.description) <= 1024, (
                f"rule_id '{rule_result.rule_id}': description "
                f"excede 1024 chars ({len(rule_result.description)})"
            )
