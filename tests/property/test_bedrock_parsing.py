"""Property tests para Bedrock Response Parsing.

**Validates: Requirements 4.4**

Property 8: Bedrock Response Parsing — JSON responses com arrays de
detecções variadas, parser extrai corretamente. Detecções válidas são
convertidas em DetectionResult; itens inválidos (match_type errado,
confidence fora do intervalo, campos faltando) são descartados.
"""

from __future__ import annotations

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.analyzer.analyzer import Analyzer
from brand_watchdog.config import AnalyzerConfig
from brand_watchdog.models.dataclasses import DetectionResult


_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# -- Estratégias de geração --


@st.composite
def valid_bounding_box(draw: st.DrawFn) -> dict:
    """Gera bounding box válido com coordenadas float entre 0 e 100."""
    return {
        "x_percent": draw(st.floats(min_value=0.0, max_value=100.0)),
        "y_percent": draw(st.floats(min_value=0.0, max_value=100.0)),
        "width_percent": draw(
            st.floats(min_value=0.0, max_value=100.0)
        ),
        "height_percent": draw(
            st.floats(min_value=0.0, max_value=100.0)
        ),
    }


@st.composite
def valid_detection_item(draw: st.DrawFn) -> dict:
    """Gera item de detecção válido conforme formato Bedrock."""
    match_type = draw(st.sampled_from(["logo", "text"]))
    confidence = draw(st.integers(min_value=0, max_value=100))
    bbox = draw(valid_bounding_box())
    description = draw(st.text(min_size=0, max_size=100))
    return {
        "match_type": match_type,
        "confidence": confidence,
        "bounding_box": bbox,
        "description": description,
    }


@st.composite
def invalid_match_type_item(draw: st.DrawFn) -> dict:
    """Gera item com match_type inválido (nem 'logo' nem 'text')."""
    invalid_type = draw(
        st.text(min_size=1, max_size=20).filter(
            lambda t: t not in ("logo", "text")
        )
    )
    bbox = draw(valid_bounding_box())
    return {
        "match_type": invalid_type,
        "confidence": draw(st.integers(min_value=0, max_value=100)),
        "bounding_box": bbox,
        "description": "item inválido",
    }


@st.composite
def invalid_confidence_item(draw: st.DrawFn) -> dict:
    """Gera item com confidence fora do intervalo 0-100."""
    confidence = draw(
        st.one_of(
            st.integers(max_value=-1),
            st.integers(min_value=101),
        )
    )
    bbox = draw(valid_bounding_box())
    return {
        "match_type": draw(st.sampled_from(["logo", "text"])),
        "confidence": confidence,
        "bounding_box": bbox,
        "description": "confidence inválida",
    }


@st.composite
def missing_fields_item(draw: st.DrawFn) -> dict:
    """Gera item com campos obrigatórios faltando."""
    # Remove um campo obrigatório aleatoriamente
    full_item = draw(valid_detection_item())
    field_to_remove = draw(
        st.sampled_from(["match_type", "confidence", "bounding_box"])
    )
    del full_item[field_to_remove]
    return full_item


@st.composite
def mixed_detection_list(draw: st.DrawFn) -> tuple[list[dict], int]:
    """Gera lista mista de detecções válidas e inválidas.

    Retorna tupla (lista_de_items, contagem_de_válidos).
    """
    valid_items = draw(
        st.lists(valid_detection_item(), min_size=0, max_size=10)
    )
    invalid_items = draw(
        st.lists(
            st.one_of(
                invalid_match_type_item(),
                invalid_confidence_item(),
                missing_fields_item(),
            ),
            min_size=0,
            max_size=5,
        )
    )

    # Intercala os itens em ordem aleatória
    all_items = valid_items + invalid_items
    shuffled = draw(st.permutations(all_items))
    return list(shuffled), len(valid_items)


def _make_analyzer() -> Analyzer:
    """Cria instância do Analyzer com configuração padrão para testes."""
    config = AnalyzerConfig()
    # Passa bedrock_client=None; não será usado no parsing
    return Analyzer(config=config, bedrock_client=None)  # type: ignore[arg-type]


class TestBedrockResponseParsing:
    """Property 8: Bedrock Response Parsing.

    JSON responses com arrays de detecções variadas, parser extrai
    corretamente todas as detecções válidas e descarta as inválidas.

    **Validates: Requirements 4.4**
    """

    @_PBT_SETTINGS
    @given(items=st.lists(valid_detection_item(), min_size=1, max_size=15))
    def test_all_valid_detections_are_parsed(
        self, items: list[dict]
    ):
        """Todas as detecções válidas devem ser convertidas em
        DetectionResult."""
        analyzer = _make_analyzer()
        response = {"detections": items}

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-123",
        )

        # Quantidade de resultados deve ser igual ao número de itens
        assert len(results) == len(items)

        # Cada resultado deve ser um DetectionResult válido
        for result in results:
            assert isinstance(result, DetectionResult)
            assert result.match_type in ("logo", "text")
            assert 0 <= result.confidence <= 100
            assert result.target_url == "https://example.com"
            assert result.screenshot_ref_id == "ref-123"
            assert result.bounding_box is not None

    @_PBT_SETTINGS
    @given(items=st.lists(invalid_match_type_item(), min_size=1, max_size=10))
    def test_invalid_match_type_items_are_skipped(
        self, items: list[dict]
    ):
        """Itens com match_type inválido devem ser descartados."""
        analyzer = _make_analyzer()
        response = {"detections": items}

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-123",
        )

        assert len(results) == 0

    @_PBT_SETTINGS
    @given(items=st.lists(invalid_confidence_item(), min_size=1, max_size=10))
    def test_invalid_confidence_items_are_skipped(
        self, items: list[dict]
    ):
        """Itens com confidence fora de 0-100 devem ser descartados."""
        analyzer = _make_analyzer()
        response = {"detections": items}

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-123",
        )

        assert len(results) == 0

    @_PBT_SETTINGS
    @given(items=st.lists(missing_fields_item(), min_size=1, max_size=10))
    def test_missing_fields_items_are_skipped(
        self, items: list[dict]
    ):
        """Itens com campos obrigatórios faltando devem ser descartados."""
        analyzer = _make_analyzer()
        response = {"detections": items}

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-123",
        )

        assert len(results) == 0

    @_PBT_SETTINGS
    @given(data=mixed_detection_list())
    def test_mixed_valid_and_invalid_counts_match(
        self, data: tuple[list[dict], int]
    ):
        """Número de resultados parseados deve ser igual ao número de
        itens válidos na entrada."""
        items, expected_valid_count = data
        analyzer = _make_analyzer()
        response = {"detections": items}

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-456",
        )

        assert len(results) == expected_valid_count

        # Todos os resultados devem ser DetectionResult válidos
        for result in results:
            assert isinstance(result, DetectionResult)
            assert result.match_type in ("logo", "text")
            assert 0 <= result.confidence <= 100

    @_PBT_SETTINGS
    @given(items=st.lists(valid_detection_item(), min_size=1, max_size=10))
    def test_parsed_values_match_input(
        self, items: list[dict]
    ):
        """Valores dos DetectionResult devem corresponder aos valores
        de entrada (match_type, confidence, bounding_box)."""
        analyzer = _make_analyzer()
        response = {"detections": items}

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://target.com",
            screenshot_ref_id="ref-789",
        )

        assert len(results) == len(items)

        for item, result in zip(items, results):
            assert result.match_type == item["match_type"]
            assert result.confidence == int(item["confidence"])
            assert (
                result.bounding_box.x_percent
                == float(item["bounding_box"]["x_percent"])
            )
            assert (
                result.bounding_box.y_percent
                == float(item["bounding_box"]["y_percent"])
            )
            assert (
                result.bounding_box.width_percent
                == float(item["bounding_box"]["width_percent"])
            )
            assert (
                result.bounding_box.height_percent
                == float(item["bounding_box"]["height_percent"])
            )
            assert result.target_url == "https://target.com"
            assert result.screenshot_ref_id == "ref-789"

    @_PBT_SETTINGS
    @given(st.data())
    def test_empty_detections_returns_empty_list(self, data):
        """Response com lista vazia ou sem 'detections' retorna lista
        vazia."""
        analyzer = _make_analyzer()
        response = data.draw(
            st.sampled_from([
                {"detections": []},
                {},
                {"detections": None},
                {"other_key": "value"},
            ])
        )

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-000",
        )

        assert results == []
