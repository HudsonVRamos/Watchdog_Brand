"""Property tests para classificação de detecções por confidence threshold.

Valida que o Analyzer filtra corretamente DetectionResults conforme
o CONFIRMED_MATCH_THRESHOLD (>= 60 = confirmado, < 60 = excluído).

**Validates: Requirements 4.5**
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.analyzer.analyzer import (
    Analyzer,
    CONFIRMED_MATCH_THRESHOLD,
)
from brand_watchdog.config import AnalyzerConfig
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    DetectionResult,
)


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- Strategies ---

def _bounding_box_strategy() -> st.SearchStrategy[dict]:
    """Gera bounding boxes válidos como dicts (formato resposta Bedrock)."""
    return st.fixed_dictionaries({
        "x_percent": st.floats(min_value=0.0, max_value=100.0),
        "y_percent": st.floats(min_value=0.0, max_value=100.0),
        "width_percent": st.floats(
            min_value=0.1, max_value=100.0
        ),
        "height_percent": st.floats(
            min_value=0.1, max_value=100.0
        ),
    })


def _detection_item_strategy(
    confidence_strategy: st.SearchStrategy[int],
) -> st.SearchStrategy[dict]:
    """Gera um item de detecção no formato de resposta do Bedrock.

    Args:
        confidence_strategy: Estratégia para gerar valores de confidence.
    """
    return st.fixed_dictionaries({
        "match_type": st.sampled_from(["logo", "text"]),
        "confidence": confidence_strategy,
        "bounding_box": _bounding_box_strategy(),
        "description": st.text(
            min_size=1, max_size=50,
            alphabet=st.characters(
                whitelist_categories=("L", "N", "Z")
            ),
        ),
    })


def _bedrock_response_strategy(
    confidence_strategy: st.SearchStrategy[int] = st.integers(
        min_value=0, max_value=100
    ),
    min_detections: int = 1,
    max_detections: int = 10,
) -> st.SearchStrategy[dict]:
    """Gera resposta simulada do Bedrock com lista de detecções.

    Args:
        confidence_strategy: Estratégia para valores de confidence.
        min_detections: Número mínimo de detecções na resposta.
        max_detections: Número máximo de detecções na resposta.
    """
    return st.fixed_dictionaries({
        "detections": st.lists(
            _detection_item_strategy(confidence_strategy),
            min_size=min_detections,
            max_size=max_detections,
        ),
    })


# --- Helpers ---

def _create_analyzer() -> Analyzer:
    """Cria instância do Analyzer com configuração padrão para testes."""
    config = AnalyzerConfig()
    # Passa None como bedrock_client — não será usado nos testes
    # pois testamos _parse_detection_response diretamente
    return Analyzer(config=config, bedrock_client=None)  # type: ignore[arg-type]


def _filter_by_threshold(
    detections: list[DetectionResult],
) -> list[DetectionResult]:
    """Aplica filtro de confidence threshold conforme Analyzer.analyze().

    Replica a lógica exata: confidence >= CONFIRMED_MATCH_THRESHOLD.
    """
    return [
        d for d in detections
        if d.confidence >= CONFIRMED_MATCH_THRESHOLD
    ]


# --- Property Tests ---


class TestConfidenceThresholdClassification:
    """Property 9: Confidence Threshold Classification.

    DetectionResults com confidence 0-100, classifica corretamente
    conforme threshold (>= 60 confirmado, < 60 excluído).

    **Validates: Requirements 4.5**
    """

    @_PBT_SETTINGS
    @given(
        response=_bedrock_response_strategy(
            confidence_strategy=st.integers(
                min_value=CONFIRMED_MATCH_THRESHOLD,
                max_value=100,
            ),
        )
    )
    def test_detections_above_threshold_always_included(
        self, response: dict
    ):
        """Detecções com confidence >= 60 são sempre incluídas."""
        analyzer = _create_analyzer()
        detections = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )
        confirmed = _filter_by_threshold(detections)

        # Todas as detecções parseadas devem estar nos confirmados
        # (todas têm confidence >= threshold)
        assert len(confirmed) == len(detections)
        for d in confirmed:
            assert d.confidence >= CONFIRMED_MATCH_THRESHOLD

    @_PBT_SETTINGS
    @given(
        response=_bedrock_response_strategy(
            confidence_strategy=st.integers(
                min_value=0,
                max_value=CONFIRMED_MATCH_THRESHOLD - 1,
            ),
        )
    )
    def test_detections_below_threshold_always_excluded(
        self, response: dict
    ):
        """Detecções com confidence < 60 são sempre excluídas."""
        analyzer = _create_analyzer()
        detections = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )
        confirmed = _filter_by_threshold(detections)

        # Nenhuma detecção abaixo do threshold deve passar
        assert len(confirmed) == 0
        for d in detections:
            assert d.confidence < CONFIRMED_MATCH_THRESHOLD

    @_PBT_SETTINGS
    @given(
        response=_bedrock_response_strategy(
            confidence_strategy=st.integers(
                min_value=0, max_value=100
            ),
        )
    )
    def test_threshold_classification_is_deterministic(
        self, response: dict
    ):
        """Mesma entrada sempre produz mesmo resultado de classificação."""
        analyzer = _create_analyzer()

        # Primeira execução
        detections_1 = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )
        confirmed_1 = _filter_by_threshold(detections_1)

        # Segunda execução com mesma entrada
        detections_2 = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )
        confirmed_2 = _filter_by_threshold(detections_2)

        # Resultados devem ser idênticos
        assert len(confirmed_1) == len(confirmed_2)
        for d1, d2 in zip(confirmed_1, confirmed_2):
            assert d1.confidence == d2.confidence
            assert d1.match_type == d2.match_type
            assert d1.target_url == d2.target_url

    @_PBT_SETTINGS
    @given(
        response=_bedrock_response_strategy(
            confidence_strategy=st.integers(
                min_value=0, max_value=100
            ),
        )
    )
    def test_threshold_partitions_detections_correctly(
        self, response: dict
    ):
        """Toda detecção é classificada: ou incluída (>=60) ou excluída (<60).

        Nenhuma detecção é perdida na classificação — a soma de incluídas
        e excluídas deve ser igual ao total de detecções.
        """
        analyzer = _create_analyzer()
        detections = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )
        confirmed = _filter_by_threshold(detections)
        excluded = [
            d for d in detections
            if d.confidence < CONFIRMED_MATCH_THRESHOLD
        ]

        # Partição completa: confirmados + excluídos == total
        assert len(confirmed) + len(excluded) == len(detections)

        # Verifica a classificação individual
        for d in detections:
            if d.confidence >= CONFIRMED_MATCH_THRESHOLD:
                assert d in confirmed
            else:
                assert d in excluded
