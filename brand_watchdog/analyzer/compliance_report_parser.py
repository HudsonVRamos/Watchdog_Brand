"""Parser de respostas do Bedrock para ComplianceReport.

Responsável por validar e transformar a resposta JSON bruta do Bedrock
em um ComplianceReport estruturado, com validações rigorosas de schema.

Requisitos cobertos: 7.1, 7.2, 7.6, 7.7
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from brand_watchdog.analyzer.compliance_exceptions import ComplianceParseError
from brand_watchdog.models.dataclasses import (
    COMPLIANCE_RULES,
    ComplianceReport,
    ComplianceRuleResult,
)

logger = logging.getLogger(__name__)

# Statuses válidos para resultados de regras
_VALID_STATUSES: set[str] = {"PASS", "FAIL", "NOT_APPLICABLE"}


class ComplianceReportParser:
    """Parser e validador de respostas do Bedrock para compliance.

    Valida a estrutura da resposta JSON e transforma em um
    ComplianceReport com todos os campos devidamente verificados.
    """

    def parse_response(
        self,
        raw_json: dict,
        target_url: str,
        screenshot_ref_id: str,
        cycle_id: str,
    ) -> ComplianceReport:
        """Parseia e valida a resposta bruta do Bedrock em ComplianceReport.

        Args:
            raw_json: Dicionário com a resposta bruta do Bedrock.
            target_url: URL do site-alvo analisado.
            screenshot_ref_id: ID de referência do screenshot.
            cycle_id: ID do ciclo de monitoramento.

        Returns:
            ComplianceReport validado e completo.

        Raises:
            ComplianceParseError: Se a resposta é inválida (schema incorreto,
                campos ausentes, valores fora do domínio, ou regras faltantes).
        """
        timestamp = datetime.now(timezone.utc)
        response_size = len(str(raw_json))

        # Validar presença da chave "compliance_results"
        if "compliance_results" not in raw_json:
            logger.error(
                "Resposta do Bedrock sem chave 'compliance_results'. "
                "Tamanho: %d bytes, Timestamp: %s",
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                "Resposta do Bedrock não contém a chave 'compliance_results'."
            )

        results_raw = raw_json["compliance_results"]

        if not isinstance(results_raw, list):
            logger.error(
                "Campo 'compliance_results' não é uma lista. "
                "Tamanho: %d bytes, Timestamp: %s",
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                "Campo 'compliance_results' deve ser uma lista."
            )

        # Parsear cada rule result individualmente
        rule_results: list[ComplianceRuleResult] = []
        for idx, item in enumerate(results_raw):
            rule_result = self._parse_rule_result(
                item, idx, response_size, timestamp
            )
            rule_results.append(rule_result)

        # Validar que todas as 6 regras configuradas estão presentes
        self._validate_all_rules_present(
            rule_results, response_size, timestamp
        )

        # Derivar overall_status
        overall_status = ComplianceReport.derive_overall_status(rule_results)

        return ComplianceReport(
            target_url=target_url,
            analyzed_at=timestamp,
            overall_status=overall_status,
            rule_results=rule_results,
            screenshot_ref_id=screenshot_ref_id,
            cycle_id=cycle_id,
        )

    def _parse_rule_result(
        self,
        item: dict,
        index: int,
        response_size: int,
        timestamp: datetime,
    ) -> ComplianceRuleResult:
        """Parseia e valida um item individual de rule result.

        Args:
            item: Dicionário com dados de uma regra.
            index: Índice do item na lista (para mensagens de erro).
            response_size: Tamanho da resposta original (para logging).
            timestamp: Timestamp do parsing (para logging).

        Returns:
            ComplianceRuleResult validado.

        Raises:
            ComplianceParseError: Se o item é inválido.
        """
        if not isinstance(item, dict):
            logger.error(
                "Item %d em 'compliance_results' não é um dicionário. "
                "Tamanho: %d bytes, Timestamp: %s",
                index,
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                f"Item {index} em 'compliance_results' não é um dicionário."
            )

        # Validar campos obrigatórios
        required_fields = ("rule_id", "status", "confidence", "description")
        for field in required_fields:
            if field not in item:
                logger.error(
                    "Item %d em 'compliance_results' sem campo '%s'. "
                    "Tamanho: %d bytes, Timestamp: %s",
                    index,
                    field,
                    response_size,
                    timestamp.isoformat(),
                )
                raise ComplianceParseError(
                    f"Item {index} em 'compliance_results' não contém "
                    f"o campo obrigatório '{field}'."
                )

        rule_id = item["rule_id"]
        status = item["status"]
        confidence = item["confidence"]
        description = item["description"]

        # Validar status
        if status not in _VALID_STATUSES:
            logger.error(
                "Item %d: status '%s' inválido. Valores aceitos: %s. "
                "Tamanho: %d bytes, Timestamp: %s",
                index,
                status,
                _VALID_STATUSES,
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                f"Item {index} (rule_id='{rule_id}'): status '{status}' "
                f"inválido. Valores aceitos: {_VALID_STATUSES}."
            )

        # Validar confidence é int 0-100
        if not isinstance(confidence, int):
            logger.error(
                "Item %d: confidence '%s' não é inteiro. "
                "Tamanho: %d bytes, Timestamp: %s",
                index,
                confidence,
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                f"Item {index} (rule_id='{rule_id}'): confidence deve ser "
                f"um inteiro, recebido: {type(confidence).__name__}."
            )

        if not (0 <= confidence <= 100):
            logger.error(
                "Item %d: confidence %d fora do intervalo [0, 100]. "
                "Tamanho: %d bytes, Timestamp: %s",
                index,
                confidence,
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                f"Item {index} (rule_id='{rule_id}'): confidence deve estar "
                f"entre 0 e 100, recebido: {confidence}."
            )

        # Validar description ≤ 1024 caracteres
        if not isinstance(description, str):
            logger.error(
                "Item %d: description não é uma string. "
                "Tamanho: %d bytes, Timestamp: %s",
                index,
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                f"Item {index} (rule_id='{rule_id}'): description deve ser "
                f"uma string."
            )

        if len(description) > 1024:
            logger.error(
                "Item %d: description excede 1024 caracteres (%d chars). "
                "Tamanho: %d bytes, Timestamp: %s",
                index,
                len(description),
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                f"Item {index} (rule_id='{rule_id}'): description excede "
                f"1024 caracteres ({len(description)} chars)."
            )

        return ComplianceRuleResult(
            rule_id=rule_id,
            status=status,
            confidence=confidence,
            description=description,
        )

    def _validate_all_rules_present(
        self,
        rule_results: list[ComplianceRuleResult],
        response_size: int,
        timestamp: datetime,
    ) -> None:
        """Valida que todas as 6 regras configuradas estão na resposta.

        Args:
            rule_results: Lista de resultados parseados.
            response_size: Tamanho da resposta original (para logging).
            timestamp: Timestamp do parsing (para logging).

        Raises:
            ComplianceParseError: Se regras estão faltando.
        """
        present_rule_ids = {r.rule_id for r in rule_results}
        expected_rule_ids = set(COMPLIANCE_RULES)
        missing_rules = expected_rule_ids - present_rule_ids

        if missing_rules:
            sorted_missing = sorted(missing_rules)
            logger.error(
                "Resposta do Bedrock com regras faltantes: %s. "
                "Regras presentes: %s. "
                "Tamanho: %d bytes, Timestamp: %s",
                sorted_missing,
                sorted(present_rule_ids),
                response_size,
                timestamp.isoformat(),
            )
            raise ComplianceParseError(
                f"Resposta do Bedrock não contém todas as regras "
                f"configuradas. Faltantes: {sorted_missing}."
            )
