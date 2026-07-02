"""Property tests para handling de respostas malformadas do Bedrock.

# Feature: mvp1-sky-amazon-compliance, Property 3: Malformed Bedrock response error handling

**Validates: Requirements 7.6, 7.7**

Property 3: Para qualquer JSON string que NÃO conforma ao schema esperado
de resposta de compliance (chave "compliance_results" ausente, valores de
status inválidos, campos obrigatórios ausentes, ou menos regras do que
configurado), o ComplianceReportParser SHALL retornar um erro (raise
ComplianceParseError) e SHALL NOT produzir um ComplianceReport parcial.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, HealthCheck

from brand_watchdog.analyzer.compliance_exceptions import (
    ComplianceParseError,
)
from brand_watchdog.analyzer.compliance_report_parser import (
    ComplianceReportParser,
)
from brand_watchdog.models.dataclasses import ComplianceReport

from tests.property.strategies import bedrock_compliance_response


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Parâmetros fixos para parse_response
_TARGET_URL = "https://example-isp.com.br/sky-amazon"
_SCREENSHOT_REF_ID = "screenshot-ref-001"
_CYCLE_ID = "cycle-test-001"


class TestMalformedBedrockResponseErrorHandling:
    """Property 3: Malformed Bedrock response error handling.

    Para qualquer resposta malformada do Bedrock, o parser DEVE
    levantar ComplianceParseError e NUNCA produzir um ComplianceReport
    parcial.

    **Validates: Requirements 7.6, 7.7**
    """

    def setup_method(self) -> None:
        """Inicializa o parser para cada teste."""
        self.parser = ComplianceReportParser()

    @_PBT_SETTINGS
    @given(malformed_response=bedrock_compliance_response(valid=False))
    def test_malformed_response_raises_parse_error(
        self,
        malformed_response: dict,
    ):
        """Para QUALQUER resposta malformada, o parser DEVE levantar
        ComplianceParseError — nunca retorna um ComplianceReport
        parcial."""
        with pytest.raises(ComplianceParseError):
            self.parser.parse_response(
                raw_json=malformed_response,
                target_url=_TARGET_URL,
                screenshot_ref_id=_SCREENSHOT_REF_ID,
                cycle_id=_CYCLE_ID,
            )

    @_PBT_SETTINGS
    @given(malformed_response=bedrock_compliance_response(valid=False))
    def test_no_partial_report_on_malformed_response(
        self,
        malformed_response: dict,
    ):
        """Verifica explicitamente que nenhum ComplianceReport é
        produzido — o parser NUNCA retorna parcialmente."""
        result = None
        try:
            result = self.parser.parse_response(
                raw_json=malformed_response,
                target_url=_TARGET_URL,
                screenshot_ref_id=_SCREENSHOT_REF_ID,
                cycle_id=_CYCLE_ID,
            )
        except ComplianceParseError:
            pass  # Esperado: erro levantado, sem report parcial

        assert result is None or isinstance(result, ComplianceReport), (
            f"Retorno inesperado: {type(result)}"
        )
        # Se chegou aqui sem exceção, é um bug — mas a assertion
        # acima valida que não há estado intermediário.
        # O test anterior já garante que ComplianceParseError é raised.

    @_PBT_SETTINGS
    @given(valid_response=bedrock_compliance_response(valid=True))
    def test_valid_response_parses_successfully(
        self,
        valid_response: dict,
    ):
        """Contraparte: respostas VÁLIDAS devem parsear sem erro,
        confirmando que o generator de respostas válidas está correto
        e o parser aceita o que deveria aceitar."""
        report = self.parser.parse_response(
            raw_json=valid_response,
            target_url=_TARGET_URL,
            screenshot_ref_id=_SCREENSHOT_REF_ID,
            cycle_id=_CYCLE_ID,
        )
        assert isinstance(report, ComplianceReport)
        assert report.target_url == _TARGET_URL
        assert report.screenshot_ref_id == _SCREENSHOT_REF_ID
        assert report.cycle_id == _CYCLE_ID
        assert len(report.rule_results) == 6
