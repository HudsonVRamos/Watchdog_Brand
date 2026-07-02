"""Testes unitários para ComplianceReportParser.

Valida parsing correto de respostas do Bedrock, tratamento de erros
e comportamento de validação para respostas inválidas.

Requisitos cobertos: 7.1, 7.2, 7.6, 7.7
"""

from __future__ import annotations

import pytest

from brand_watchdog.analyzer.compliance_exceptions import ComplianceParseError
from brand_watchdog.analyzer.compliance_report_parser import (
    ComplianceReportParser,
)
from brand_watchdog.models.dataclasses import COMPLIANCE_RULES


@pytest.fixture
def parser() -> ComplianceReportParser:
    """Instância do parser para testes."""
    return ComplianceReportParser()


@pytest.fixture
def valid_bedrock_response() -> dict:
    """Resposta válida do Bedrock com todas as 6 regras."""
    return {
        "compliance_results": [
            {
                "rule_id": "facilitator_role",
                "status": "PASS",
                "confidence": 92,
                "description": "Todas as menções de Amazon Prime estão associadas a SKY+.",
            },
            {
                "rule_id": "logo_application",
                "status": "PASS",
                "confidence": 88,
                "description": "Logos aplicados na ordem correta com separador.",
            },
            {
                "rule_id": "logo_effects",
                "status": "FAIL",
                "confidence": 75,
                "description": "Efeito de sombra detectado no logo SKY+.",
            },
            {
                "rule_id": "content_separation",
                "status": "PASS",
                "confidence": 95,
                "description": "Conteúdo parceiro separado por blocos distintos.",
            },
            {
                "rule_id": "naming_pricing",
                "status": "PASS",
                "confidence": 90,
                "description": "Nomenclatura correta e preço acima de R$80.",
            },
            {
                "rule_id": "kv_integrity",
                "status": "NOT_APPLICABLE",
                "confidence": 100,
                "description": "Nenhum KV detectado na página.",
            },
        ]
    }


@pytest.fixture
def metadata() -> dict:
    """Metadados para construção do ComplianceReport."""
    return {
        "target_url": "https://isp-example.com/sky-amazon",
        "screenshot_ref_id": "scr-abc123",
        "cycle_id": "cycle-001",
    }


class TestParseResponseValid:
    """Testes para parsing de respostas válidas."""

    def test_parse_valid_response_returns_compliance_report(
        self, parser: ComplianceReportParser, valid_bedrock_response: dict, metadata: dict
    ) -> None:
        """Parsing de resposta válida retorna ComplianceReport correto."""
        report = parser.parse_response(
            valid_bedrock_response, **metadata
        )

        assert report.target_url == metadata["target_url"]
        assert report.screenshot_ref_id == metadata["screenshot_ref_id"]
        assert report.cycle_id == metadata["cycle_id"]
        assert len(report.rule_results) == 6

    def test_parse_valid_response_derives_non_compliant_status(
        self, parser: ComplianceReportParser, valid_bedrock_response: dict, metadata: dict
    ) -> None:
        """Se alguma regra FAIL, overall_status deve ser 'non_compliant'."""
        report = parser.parse_response(
            valid_bedrock_response, **metadata
        )
        # A fixture tem logo_effects com FAIL
        assert report.overall_status == "non_compliant"

    def test_parse_all_pass_derives_compliant_status(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Se todas as regras PASS, overall_status deve ser 'compliant'."""
        response = {
            "compliance_results": [
                {
                    "rule_id": rule,
                    "status": "PASS",
                    "confidence": 85,
                    "description": f"Regra {rule} aprovada.",
                }
                for rule in COMPLIANCE_RULES
            ]
        }
        report = parser.parse_response(response, **metadata)
        assert report.overall_status == "compliant"

    def test_parse_response_preserves_rule_results_data(
        self, parser: ComplianceReportParser, valid_bedrock_response: dict, metadata: dict
    ) -> None:
        """Dados dos rule results são preservados corretamente."""
        report = parser.parse_response(
            valid_bedrock_response, **metadata
        )

        first_rule = report.rule_results[0]
        assert first_rule.rule_id == "facilitator_role"
        assert first_rule.status == "PASS"
        assert first_rule.confidence == 92
        assert "Amazon Prime" in first_rule.description

    def test_parse_response_sets_analyzed_at(
        self, parser: ComplianceReportParser, valid_bedrock_response: dict, metadata: dict
    ) -> None:
        """Campo analyzed_at é preenchido com timestamp UTC."""
        report = parser.parse_response(
            valid_bedrock_response, **metadata
        )
        assert report.analyzed_at is not None
        assert report.analyzed_at.tzinfo is not None


class TestParseResponseInvalidStructure:
    """Testes para respostas com estrutura inválida."""

    def test_missing_compliance_results_key(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Resposta sem chave 'compliance_results' levanta ComplianceParseError."""
        with pytest.raises(ComplianceParseError, match="compliance_results"):
            parser.parse_response({"other_key": []}, **metadata)

    def test_compliance_results_not_a_list(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Resposta com 'compliance_results' não-lista levanta ComplianceParseError."""
        with pytest.raises(ComplianceParseError, match="lista"):
            parser.parse_response(
                {"compliance_results": "not a list"}, **metadata
            )

    def test_item_not_a_dict(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Item que não é dicionário levanta ComplianceParseError."""
        with pytest.raises(ComplianceParseError, match="dicionário"):
            parser.parse_response(
                {"compliance_results": ["not a dict"]}, **metadata
            )

    def test_empty_response(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Resposta vazia (dict vazio) levanta ComplianceParseError."""
        with pytest.raises(ComplianceParseError):
            parser.parse_response({}, **metadata)


class TestParseResponseMissingFields:
    """Testes para respostas com campos obrigatórios ausentes."""

    @pytest.mark.parametrize("missing_field", [
        "rule_id", "status", "confidence", "description",
    ])
    def test_missing_required_field(
        self, parser: ComplianceReportParser, metadata: dict, missing_field: str
    ) -> None:
        """Ausência de campo obrigatório levanta ComplianceParseError."""
        item = {
            "rule_id": "facilitator_role",
            "status": "PASS",
            "confidence": 90,
            "description": "Descrição.",
        }
        del item[missing_field]

        response = {"compliance_results": [item]}
        with pytest.raises(ComplianceParseError, match=missing_field):
            parser.parse_response(response, **metadata)


class TestParseResponseInvalidValues:
    """Testes para respostas com valores fora do domínio."""

    def test_invalid_status_value(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Status inválido levanta ComplianceParseError."""
        response = {
            "compliance_results": [
                {
                    "rule_id": "facilitator_role",
                    "status": "INVALID",
                    "confidence": 90,
                    "description": "Teste.",
                }
            ]
        }
        with pytest.raises(ComplianceParseError, match="status"):
            parser.parse_response(response, **metadata)

    def test_confidence_not_integer(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Confidence não-inteiro levanta ComplianceParseError."""
        response = {
            "compliance_results": [
                {
                    "rule_id": "facilitator_role",
                    "status": "PASS",
                    "confidence": 90.5,
                    "description": "Teste.",
                }
            ]
        }
        with pytest.raises(ComplianceParseError, match="confidence"):
            parser.parse_response(response, **metadata)

    def test_confidence_below_zero(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Confidence abaixo de 0 levanta ComplianceParseError."""
        response = {
            "compliance_results": [
                {
                    "rule_id": "facilitator_role",
                    "status": "PASS",
                    "confidence": -1,
                    "description": "Teste.",
                }
            ]
        }
        with pytest.raises(ComplianceParseError, match="confidence"):
            parser.parse_response(response, **metadata)

    def test_confidence_above_100(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Confidence acima de 100 levanta ComplianceParseError."""
        response = {
            "compliance_results": [
                {
                    "rule_id": "facilitator_role",
                    "status": "PASS",
                    "confidence": 101,
                    "description": "Teste.",
                }
            ]
        }
        with pytest.raises(ComplianceParseError, match="confidence"):
            parser.parse_response(response, **metadata)

    def test_description_exceeds_1024_chars(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Description com mais de 1024 caracteres levanta ComplianceParseError."""
        response = {
            "compliance_results": [
                {
                    "rule_id": "facilitator_role",
                    "status": "PASS",
                    "confidence": 90,
                    "description": "x" * 1025,
                }
            ]
        }
        with pytest.raises(ComplianceParseError, match="1024"):
            parser.parse_response(response, **metadata)

    def test_description_not_string(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Description não-string levanta ComplianceParseError."""
        response = {
            "compliance_results": [
                {
                    "rule_id": "facilitator_role",
                    "status": "PASS",
                    "confidence": 90,
                    "description": 12345,
                }
            ]
        }
        with pytest.raises(ComplianceParseError, match="description"):
            parser.parse_response(response, **metadata)


class TestParseResponseMissingRules:
    """Testes para respostas com regras faltantes."""

    def test_missing_one_rule(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Resposta sem uma regra configurada levanta ComplianceParseError."""
        # Apenas 5 regras (falta kv_integrity)
        rules_without_last = COMPLIANCE_RULES[:-1]
        response = {
            "compliance_results": [
                {
                    "rule_id": rule,
                    "status": "PASS",
                    "confidence": 85,
                    "description": f"Regra {rule} OK.",
                }
                for rule in rules_without_last
            ]
        }
        with pytest.raises(ComplianceParseError, match="kv_integrity"):
            parser.parse_response(response, **metadata)

    def test_empty_compliance_results(
        self, parser: ComplianceReportParser, metadata: dict
    ) -> None:
        """Lista vazia de results levanta ComplianceParseError (todas faltam)."""
        response = {"compliance_results": []}
        with pytest.raises(ComplianceParseError, match="Faltantes"):
            parser.parse_response(response, **metadata)
