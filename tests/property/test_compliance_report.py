"""Property test para rastreabilidade de versão no ComplianceReport.

# Feature: architecture-evolution, Property 11: Rastreabilidade de Versão no ComplianceReport

**Validates: Requirements 7.3**

Property 11: Para qualquer ComplianceReport gerado durante um ciclo,
o campo `rule_set_version` SHALL estar presente e ser igual ao
`rule_set_version` registrado no MonitoringCycleModel correspondente.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.models.dataclasses import (
    ComplianceReport,
    ComplianceRuleResult,
    COMPLIANCE_RULES,
)

from tests.property.strategies import compliance_rule_result


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Regex do formato rule_set_version: "v{timestamp_unix}_{hash_8_chars}"
_RULE_SET_VERSION_PATTERN = re.compile(r"^v\d{10}_[0-9a-f]{8}$")


# -- Estratégias locais --


@st.composite
def _rule_set_version(draw: st.DrawFn) -> str:
    """Gera uma rule_set_version válida no formato 'v{timestamp_unix}_{hash_8_chars}'."""
    return draw(
        st.from_regex(r"v[0-9]{10}_[0-9a-f]{8}", fullmatch=True)
    )


@st.composite
def _compliance_report_with_version(
    draw: st.DrawFn,
    version: str | None = None,
) -> tuple[ComplianceReport, str]:
    """Gera um ComplianceReport com rule_set_version associado.

    Simula o cenário em que o Coordinator cria um ciclo com rule_set_version
    e o Worker gera o ComplianceReport propagando essa versão.

    Returns:
        Tupla (ComplianceReport, rule_set_version_do_ciclo).
    """
    # Versão do ciclo (simulando MonitoringCycleModel.rule_set_version)
    cycle_version = version if version is not None else draw(_rule_set_version())

    # Gerar rule_results com as 6 regras
    rule_results = []
    for rule_id in COMPLIANCE_RULES:
        result = draw(compliance_rule_result())
        rule_results.append(
            ComplianceRuleResult(
                rule_id=rule_id,
                status=result.status,
                confidence=result.confidence,
                description=result.description,
            )
        )

    overall_status = ComplianceReport.derive_overall_status(rule_results)

    target_url = draw(
        st.from_regex(
            r"https://[a-z]{3,10}\.[a-z]{2,5}/[a-z]{1,10}",
            fullmatch=True,
        )
    )
    analyzed_at = draw(
        st.datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 12, 31),
            timezones=st.just(timezone.utc),
        )
    )
    screenshot_ref_id = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=5,
            max_size=30,
        )
    )
    cycle_id = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=5,
            max_size=30,
        )
    )

    # Propagar rule_set_version do ciclo para o report
    report = ComplianceReport(
        target_url=target_url,
        analyzed_at=analyzed_at,
        overall_status=overall_status,
        rule_results=rule_results,
        screenshot_ref_id=screenshot_ref_id,
        cycle_id=cycle_id,
        rule_set_version=cycle_version,
    )

    return report, cycle_version


class TestComplianceReportVersionTraceability:
    """Property 11: Rastreabilidade de Versão no ComplianceReport.

    Para qualquer ComplianceReport gerado durante um ciclo, o campo
    `rule_set_version` SHALL estar presente e ser igual ao
    `rule_set_version` registrado no MonitoringCycleModel correspondente.

    **Validates: Requirements 7.3**
    """

    @_PBT_SETTINGS
    @given(data=st.data())
    def test_rule_set_version_present_in_report(self, data: st.DataObject) -> None:
        """O campo rule_set_version deve estar presente (não-vazio) no report."""
        report, cycle_version = data.draw(_compliance_report_with_version())

        assert hasattr(report, "rule_set_version"), (
            "ComplianceReport não possui o campo 'rule_set_version'"
        )
        assert report.rule_set_version != "", (
            "ComplianceReport.rule_set_version está vazio, deveria conter "
            f"a versão do ciclo: '{cycle_version}'"
        )

    @_PBT_SETTINGS
    @given(data=st.data())
    def test_rule_set_version_matches_cycle(self, data: st.DataObject) -> None:
        """O rule_set_version do report deve ser igual ao do ciclo correspondente."""
        report, cycle_version = data.draw(_compliance_report_with_version())

        assert report.rule_set_version == cycle_version, (
            f"rule_set_version do report '{report.rule_set_version}' "
            f"difere da versão do ciclo '{cycle_version}'"
        )

    @_PBT_SETTINGS
    @given(data=st.data())
    def test_rule_set_version_format_valid(self, data: st.DataObject) -> None:
        """O rule_set_version deve seguir o formato 'v{timestamp_unix}_{hash_8_chars}'."""
        report, _ = data.draw(_compliance_report_with_version())

        assert _RULE_SET_VERSION_PATTERN.match(report.rule_set_version), (
            f"rule_set_version '{report.rule_set_version}' não segue o formato "
            f"esperado 'v{{timestamp_unix}}_{{hash_8_chars}}'"
        )

    @_PBT_SETTINGS
    @given(data=st.data())
    def test_rule_set_version_preserved_in_serialization(
        self, data: st.DataObject
    ) -> None:
        """O rule_set_version deve ser preservado após serialização/deserialização."""
        report, cycle_version = data.draw(_compliance_report_with_version())

        serialized = report.to_dict()
        deserialized = ComplianceReport.from_dict(serialized)

        assert deserialized.rule_set_version == cycle_version, (
            f"rule_set_version perdido na serialização: "
            f"original='{cycle_version}', "
            f"deserializado='{deserialized.rule_set_version}'"
        )

    @_PBT_SETTINGS
    @given(version=_rule_set_version())
    def test_rule_set_version_not_mutated_by_report_creation(
        self, version: str
    ) -> None:
        """A criação do ComplianceReport não deve mutar o rule_set_version fornecido."""
        report = ComplianceReport(
            target_url="https://example.com/test",
            analyzed_at=datetime.now(timezone.utc),
            overall_status="compliant",
            rule_results=[],
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
            rule_set_version=version,
        )

        assert report.rule_set_version == version, (
            f"rule_set_version mutado: entrada='{version}', "
            f"no report='{report.rule_set_version}'"
        )
