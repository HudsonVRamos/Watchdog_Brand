"""Estratégias Hypothesis para geração de dados de compliance.

Generators reutilizáveis para os property tests do módulo de compliance
SKY+/Amazon Prime.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hypothesis import strategies as st

from brand_watchdog.models.dataclasses import (
    COMPLIANCE_RULES,
    ComplianceReport,
    ComplianceRuleResult,
)


# Statuses válidos para ComplianceRuleResult
_VALID_STATUSES = ("PASS", "FAIL", "NOT_APPLICABLE")


@st.composite
def compliance_rule_result(
    draw: st.DrawFn,
    *,
    status: str | None = None,
) -> ComplianceRuleResult:
    """Gera um ComplianceRuleResult válido.

    Args:
        status: Se fornecido, fixa o status ao invés de gerar aleatório.
    """
    rule_id = draw(st.sampled_from(COMPLIANCE_RULES))
    rule_status = status if status is not None else draw(
        st.sampled_from(list(_VALID_STATUSES))
    )
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
        status=rule_status,
        confidence=confidence,
        description=description,
    )


@st.composite
def compliance_rule_result_list(
    draw: st.DrawFn,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> list[ComplianceRuleResult]:
    """Gera uma lista de ComplianceRuleResult válidos."""
    return draw(
        st.lists(
            compliance_rule_result(),
            min_size=min_size,
            max_size=max_size,
        )
    )


@st.composite
def compliance_rule_result_list_with_at_least_one_fail(
    draw: st.DrawFn,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> list[ComplianceRuleResult]:
    """Gera lista de ComplianceRuleResult com pelo menos um FAIL."""
    # Garante pelo menos um FAIL
    fail_result = draw(compliance_rule_result(status="FAIL"))

    # Gera resultados adicionais com qualquer status
    others = draw(
        st.lists(
            compliance_rule_result(),
            min_size=max(0, min_size - 1),
            max_size=max_size - 1,
        )
    )

    # Combina e embaralha
    all_results = [fail_result] + others
    shuffled = draw(st.permutations(all_results))
    return list(shuffled)


@st.composite
def compliance_rule_result_list_without_fail(
    draw: st.DrawFn,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> list[ComplianceRuleResult]:
    """Gera lista de ComplianceRuleResult sem nenhum FAIL (apenas PASS/NOT_APPLICABLE)."""
    return draw(
        st.lists(
            compliance_rule_result(
                status=draw(st.sampled_from(["PASS", "NOT_APPLICABLE"]))
            ),
            min_size=min_size,
            max_size=max_size,
        )
    )


@st.composite
def analyzed_at_datetime(draw: st.DrawFn) -> datetime:
    """Gera um datetime válido para analyzed_at."""
    return draw(
        st.datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 12, 31),
            timezones=st.just(timezone.utc),
        )
    )


# -- Generators para respostas Bedrock (válidas e inválidas) --

# Statuses inválidos para testar rejeição
_INVALID_STATUSES = (
    "pass", "fail", "INVALID", "ERROR", "OK",
    "not_applicable", "Pass", "Fail", "", "0", "1",
)


@st.composite
def _valid_rule_result_dict(
    draw: st.DrawFn,
    *,
    rule_id: str | None = None,
) -> dict[str, Any]:
    """Gera um dicionário de rule result válido (schema correto)."""
    rid = rule_id or draw(st.sampled_from(COMPLIANCE_RULES))
    status = draw(st.sampled_from(list(_VALID_STATUSES)))
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
    return {
        "rule_id": rid,
        "status": status,
        "confidence": confidence,
        "description": description,
    }


@st.composite
def _malformed_rule_result_dict(draw: st.DrawFn) -> dict[str, Any]:
    """Gera um dicionário de rule result com uma violação de schema.

    Escolhe aleatoriamente um tipo de violação dentre:
    - Campo obrigatório ausente
    - Status inválido
    - Confidence não-int ou fora do range
    - Description excedendo 1024 chars
    """
    violation_type = draw(st.sampled_from([
        "missing_rule_id",
        "missing_status",
        "missing_confidence",
        "missing_description",
        "invalid_status",
        "confidence_not_int",
        "confidence_out_of_range",
        "description_too_long",
    ]))

    # Base válida para modificar
    base = {
        "rule_id": draw(st.sampled_from(COMPLIANCE_RULES)),
        "status": draw(st.sampled_from(list(_VALID_STATUSES))),
        "confidence": draw(st.integers(min_value=0, max_value=100)),
        "description": "Test description",
    }

    if violation_type == "missing_rule_id":
        del base["rule_id"]
    elif violation_type == "missing_status":
        del base["status"]
    elif violation_type == "missing_confidence":
        del base["confidence"]
    elif violation_type == "missing_description":
        del base["description"]
    elif violation_type == "invalid_status":
        base["status"] = draw(st.sampled_from(_INVALID_STATUSES))
    elif violation_type == "confidence_not_int":
        base["confidence"] = draw(st.one_of(
            st.text(min_size=1, max_size=5),
            st.floats(allow_nan=False, allow_infinity=False),
            st.none(),
            st.lists(st.integers(), max_size=2),
        ))
    elif violation_type == "confidence_out_of_range":
        base["confidence"] = draw(st.one_of(
            st.integers(max_value=-1),
            st.integers(min_value=101),
        ))
    elif violation_type == "description_too_long":
        base["description"] = draw(
            st.text(min_size=1025, max_size=2000)
        )

    return base


@st.composite
def bedrock_compliance_response(
    draw: st.DrawFn,
    *,
    valid: bool = False,
) -> dict[str, Any]:
    """Gera respostas Bedrock de compliance (válidas ou inválidas).

    Se valid=True, gera uma resposta completamente válida com todas
    as 6 regras configuradas.

    Se valid=False (padrão), gera uma resposta malformada com pelo
    menos uma violação de schema dentre:
    - Chave "compliance_results" ausente
    - compliance_results não é uma lista
    - Items que não são dicts
    - Campos obrigatórios ausentes
    - Valores de status inválidos
    - Confidence não-int ou fora do range [0, 100]
    - Menos regras do que as 6 configuradas
    - Description excedendo 1024 chars
    """
    if valid:
        # Gera resposta completamente válida
        results = []
        for rule_id in COMPLIANCE_RULES:
            result = draw(_valid_rule_result_dict(rule_id=rule_id))
            results.append(result)
        return {"compliance_results": results}

    # Gera resposta malformada
    malformation_type = draw(st.sampled_from([
        "missing_key",
        "not_a_list",
        "item_not_dict",
        "malformed_item",
        "fewer_rules",
    ]))

    if malformation_type == "missing_key":
        # Chave compliance_results ausente
        other_keys = draw(st.dictionaries(
            keys=st.text(min_size=1, max_size=20).filter(
                lambda k: k != "compliance_results"
            ),
            values=st.text(min_size=1, max_size=50),
            min_size=0,
            max_size=3,
        ))
        return other_keys

    elif malformation_type == "not_a_list":
        # compliance_results não é uma lista
        non_list_value = draw(st.one_of(
            st.text(min_size=0, max_size=50),
            st.integers(),
            st.none(),
            st.dictionaries(
                keys=st.text(min_size=1, max_size=10),
                values=st.text(min_size=1, max_size=10),
                max_size=3,
            ),
            st.booleans(),
        ))
        return {"compliance_results": non_list_value}

    elif malformation_type == "item_not_dict":
        # Pelo menos um item não é dict
        non_dict_item = draw(st.one_of(
            st.text(min_size=0, max_size=50),
            st.integers(),
            st.none(),
            st.lists(st.integers(), max_size=3),
            st.booleans(),
        ))
        # Pode ter items válidos antes do inválido
        valid_items_before = draw(st.integers(
            min_value=0, max_value=2
        ))
        items: list[Any] = []
        for _ in range(valid_items_before):
            items.append(draw(_valid_rule_result_dict()))
        items.append(non_dict_item)
        return {"compliance_results": items}

    elif malformation_type == "malformed_item":
        # Items são dicts mas com schema inválido
        malformed = draw(_malformed_rule_result_dict())
        # Pode ter items válidos antes
        valid_items_before = draw(st.integers(
            min_value=0, max_value=3
        ))
        items_list: list[Any] = []
        for _ in range(valid_items_before):
            items_list.append(draw(_valid_rule_result_dict()))
        items_list.append(malformed)
        return {"compliance_results": items_list}

    else:  # fewer_rules
        # Menos regras do que as 6 configuradas (1 a 5 regras)
        num_rules = draw(st.integers(min_value=1, max_value=5))
        subset = draw(
            st.lists(
                st.sampled_from(COMPLIANCE_RULES),
                min_size=num_rules,
                max_size=num_rules,
                unique=True,
            )
        )
        results = []
        for rule_id in subset:
            results.append(draw(_valid_rule_result_dict(rule_id=rule_id)))
        return {"compliance_results": results}


@st.composite
def compliance_report(draw: st.DrawFn) -> ComplianceReport:
    """Gera um ComplianceReport válido com todas as 6 regras.

    O report contém exatamente uma ComplianceRuleResult por regra
    configurada, com overall_status derivado automaticamente.
    """
    rule_results = []
    for rule_id in COMPLIANCE_RULES:
        result = draw(compliance_rule_result())
        # Substitui rule_id para garantir todas as 6 regras
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
    analyzed_at = draw(analyzed_at_datetime())
    screenshot_ref_id = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
            ),
            min_size=5,
            max_size=30,
        )
    )
    cycle_id = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
            ),
            min_size=5,
            max_size=30,
        )
    )

    return ComplianceReport(
        target_url=target_url,
        analyzed_at=analyzed_at,
        overall_status=overall_status,
        rule_results=rule_results,
        screenshot_ref_id=screenshot_ref_id,
        cycle_id=cycle_id,
    )


@st.composite
def image_with_label(
    draw: st.DrawFn,
    *,
    max_image_size: int = 100 * 1024,
) -> tuple[bytes, str]:
    """Gera uma tupla (image_bytes, label) dentro dos limites de 5MB.

    Args:
        max_image_size: Tamanho máximo dos bytes gerados (padrão 100KB
            para performance nos testes).

    Returns:
        Tupla (image_bytes, label) com bytes não-vazios e label não-vazio.
    """
    image_bytes = draw(
        st.binary(min_size=1, max_size=max_image_size)
    )
    label = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P"),
            ),
            min_size=1,
            max_size=50,
        )
    )
    return (image_bytes, label)


@st.composite
def reference_image_availability(
    draw: st.DrawFn,
) -> frozenset[str]:
    """Gera um subconjunto de imagens de referência presentes no filesystem.

    Retorna frozenset com 0 a 3 nomes de arquivos de referência que
    devem existir para o teste. Imagens ausentes do set simulam
    arquivos faltantes ou ilegíveis.
    """
    all_images = [
        "Artes_aprovadas_referencia.PNG",
        "Logo_errado_logo_correto.PNG",
        "logo_sky_plus_amazon.PNG",
    ]
    available = draw(
        st.frozensets(
            st.sampled_from(all_images),
            min_size=0,
            max_size=len(all_images),
        )
    )
    return available


@st.composite
def valid_bedrock_compliance_response(
    draw: st.DrawFn,
) -> dict[str, Any]:
    """Gera uma resposta Bedrock válida com exatamente 6 regras.

    Cada regra em COMPLIANCE_RULES terá um resultado com campos válidos:
    - rule_id: um dos COMPLIANCE_RULES
    - status: "PASS", "FAIL", ou "NOT_APPLICABLE"
    - confidence: int em [0, 100]
    - description: string não-vazia com ≤ 1024 caracteres
    """
    results = []
    for rule_id in COMPLIANCE_RULES:
        status = draw(st.sampled_from(list(_VALID_STATUSES)))
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
        results.append({
            "rule_id": rule_id,
            "status": status,
            "confidence": confidence,
            "description": description,
        })
    return {"compliance_results": results}
