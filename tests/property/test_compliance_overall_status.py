"""Property tests para derivação de overall_status do ComplianceReport.

# Feature: mvp1-sky-amazon-compliance, Property 2: Overall compliance status derivation

**Validates: Requirements 7.3, 7.4**

Property 2: Para qualquer lista de ComplianceRuleResult, o overall_status
derivado é "non_compliant" se e somente se pelo menos uma regra tem status
"FAIL"; caso contrário, é "compliant". Isso vale independente de valores de
confidence, conteúdo de description, ou número de regras.
"""

from __future__ import annotations

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.models.dataclasses import (
    ComplianceReport,
    ComplianceRuleResult,
    COMPLIANCE_RULES,
)

from tests.property.strategies import (
    compliance_rule_result,
    compliance_rule_result_list_with_at_least_one_fail,
)


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# -- Estratégia local para listas sem FAIL --


@st.composite
def _rule_result_no_fail(draw: st.DrawFn) -> ComplianceRuleResult:
    """Gera ComplianceRuleResult com status PASS ou NOT_APPLICABLE (nunca FAIL)."""
    rule_id = draw(st.sampled_from(COMPLIANCE_RULES))
    status = draw(st.sampled_from(["PASS", "NOT_APPLICABLE"]))
    confidence = draw(st.integers(min_value=0, max_value=100))
    description = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "Z"),
            ),
            min_size=1,
            max_size=200,
        )
    )
    return ComplianceRuleResult(
        rule_id=rule_id,
        status=status,
        confidence=confidence,
        description=description,
    )


class TestOverallComplianceStatusDerivation:
    """Property 2: Overall compliance status derivation.

    O overall_status derivado é "non_compliant" sse pelo menos uma regra
    tem status "FAIL"; caso contrário é "compliant".

    **Validates: Requirements 7.3, 7.4**
    """

    @_PBT_SETTINGS
    @given(rule_results=compliance_rule_result_list_with_at_least_one_fail())
    def test_at_least_one_fail_produces_non_compliant(
        self,
        rule_results: list[ComplianceRuleResult],
    ):
        """Se pelo menos uma regra tem status FAIL, overall_status deve ser
        'non_compliant'."""
        result = ComplianceReport.derive_overall_status(rule_results)
        assert result == "non_compliant", (
            f"Esperado 'non_compliant' para lista com FAIL, obteve '{result}'. "
            f"Statuses: {[r.status for r in rule_results]}"
        )

    @_PBT_SETTINGS
    @given(
        rule_results=st.lists(
            _rule_result_no_fail(),
            min_size=1,
            max_size=10,
        )
    )
    def test_no_fail_produces_compliant(
        self,
        rule_results: list[ComplianceRuleResult],
    ):
        """Se nenhuma regra tem status FAIL, overall_status deve ser
        'compliant'."""
        # Confirma pré-condição: nenhum FAIL na lista
        assert all(r.status != "FAIL" for r in rule_results)

        result = ComplianceReport.derive_overall_status(rule_results)
        assert result == "compliant", (
            f"Esperado 'compliant' para lista sem FAIL, obteve '{result}'. "
            f"Statuses: {[r.status for r in rule_results]}"
        )

    @_PBT_SETTINGS
    @given(rule_results=st.lists(compliance_rule_result(), min_size=1, max_size=15))
    def test_biconditional_fail_iff_non_compliant(
        self,
        rule_results: list[ComplianceRuleResult],
    ):
        """Propriedade bicondicional: overall_status == 'non_compliant'
        se e somente se existe pelo menos uma regra com status FAIL."""
        result = ComplianceReport.derive_overall_status(rule_results)
        has_fail = any(r.status == "FAIL" for r in rule_results)

        if has_fail:
            assert result == "non_compliant", (
                f"Lista contém FAIL mas resultado é '{result}'"
            )
        else:
            assert result == "compliant", (
                f"Lista não contém FAIL mas resultado é '{result}'"
            )

    @_PBT_SETTINGS
    @given(rule_results=st.lists(compliance_rule_result(), min_size=0, max_size=15))
    def test_empty_list_produces_compliant(
        self,
        rule_results: list[ComplianceRuleResult],
    ):
        """Lista vazia (sem regras) deve produzir 'compliant', e para
        qualquer lista gerada, a propriedade bicondicional deve valer."""
        result = ComplianceReport.derive_overall_status(rule_results)
        has_fail = any(r.status == "FAIL" for r in rule_results)

        # Bicondicional: FAIL ↔ non_compliant
        assert (result == "non_compliant") == has_fail
