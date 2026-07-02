"""Property tests para Duplicate Alert Suppression.

**Validates: Requirements 6.7**

Property 14: Duplicate Alert Suppression —
Pares de detecções (current vs previous), suprime corretamente duplicatas.

Garante que:
- Detecções com MESMO match_type E bounding boxes sobrepostos
  (todas coords diferem por <= 5%) SÃO suprimidas
- Detecções com match_type DIFERENTE NUNCA são suprimidas
- Detecções com bounding boxes não sobrepostos (alguma coord
  difere por > 5%) NÃO são suprimidas
- Quando não há detecções anteriores, nada é suprimido
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.alerts.alert_service import (
    AlertService,
    _BBOX_TOLERANCE,
)
from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    DetectionResult,
)
from brand_watchdog.storage.detection_store import DetectionStore


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- Strategies ---

_match_type_st = st.sampled_from(["logo", "text"])

_bbox_coord_st = st.floats(
    min_value=0.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
)

_target_url_st = st.sampled_from([
    "https://example.com",
    "https://test.org/page",
    "https://site.net/path/to/page",
])

_description_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=100,
)

_detected_at_st = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(timezone.utc),
)


@st.composite
def bounding_box_st(draw: st.DrawFn) -> BoundingBox:
    """Gera um BoundingBox com coordenadas aleatórias válidas."""
    return BoundingBox(
        x_percent=draw(_bbox_coord_st),
        y_percent=draw(_bbox_coord_st),
        width_percent=draw(_bbox_coord_st),
        height_percent=draw(_bbox_coord_st),
    )


@st.composite
def overlapping_bbox_pair_st(draw: st.DrawFn) -> tuple[BoundingBox, BoundingBox]:
    """Gera par de BoundingBoxes que se sobrepõem (diferença <= 5% em cada coord).

    Gera um box base e cria um segundo com offsets dentro da tolerância.
    """
    base = draw(bounding_box_st())

    # Offsets dentro da tolerância (inclusive no limite)
    offset_st = st.floats(
        min_value=-_BBOX_TOLERANCE,
        max_value=_BBOX_TOLERANCE,
        allow_nan=False,
        allow_infinity=False,
    )

    x_offset = draw(offset_st)
    y_offset = draw(offset_st)
    w_offset = draw(offset_st)
    h_offset = draw(offset_st)

    # Clamp para [0, 100] para manter coordenadas válidas
    second = BoundingBox(
        x_percent=max(0.0, min(100.0, base.x_percent + x_offset)),
        y_percent=max(0.0, min(100.0, base.y_percent + y_offset)),
        width_percent=max(0.0, min(100.0, base.width_percent + w_offset)),
        height_percent=max(0.0, min(100.0, base.height_percent + h_offset)),
    )

    return base, second


@st.composite
def non_overlapping_bbox_pair_st(
    draw: st.DrawFn,
) -> tuple[BoundingBox, BoundingBox]:
    """Gera par de BoundingBoxes que NÃO se sobrepõem.

    Garante que pelo menos uma coordenada difere por mais de 5%.
    """
    base = draw(bounding_box_st())

    # Escolhe pelo menos uma coordenada que excede a tolerância
    # Gera offset > 5% (com margem para garantir)
    big_offset_st = st.one_of(
        st.floats(
            min_value=_BBOX_TOLERANCE + 0.01,
            max_value=50.0,
            allow_nan=False,
            allow_infinity=False,
        ),
        st.floats(
            min_value=-50.0,
            max_value=-(_BBOX_TOLERANCE + 0.01),
            allow_nan=False,
            allow_infinity=False,
        ),
    )

    # Offset que pode estar dentro ou fora da tolerância
    any_offset_st = st.floats(
        min_value=-50.0,
        max_value=50.0,
        allow_nan=False,
        allow_infinity=False,
    )

    # Escolhe qual coordenada vai exceder a tolerância
    coord_to_exceed = draw(st.integers(min_value=0, max_value=3))

    offsets = [draw(any_offset_st) for _ in range(4)]
    # Garante que pelo menos uma coordenada excede tolerância
    offsets[coord_to_exceed] = draw(big_offset_st)

    second = BoundingBox(
        x_percent=max(0.0, min(100.0, base.x_percent + offsets[0])),
        y_percent=max(0.0, min(100.0, base.y_percent + offsets[1])),
        width_percent=max(0.0, min(100.0, base.width_percent + offsets[2])),
        height_percent=max(0.0, min(100.0, base.height_percent + offsets[3])),
    )

    return base, second


@st.composite
def detection_result_st(
    draw: st.DrawFn,
    target_url: str | None = None,
    match_type: str | None = None,
    bbox: BoundingBox | None = None,
) -> DetectionResult:
    """Gera um DetectionResult com campos opcionais fixos."""
    return DetectionResult(
        target_url=target_url or draw(_target_url_st),
        match_type=match_type or draw(_match_type_st),
        confidence=draw(st.integers(min_value=0, max_value=100)),
        bounding_box=bbox or draw(bounding_box_st()),
        description=draw(_description_st),
        detected_at=draw(_detected_at_st),
        screenshot_ref_id="screenshot-001",
    )


def _make_alert_service() -> AlertService:
    """Cria AlertService com mock do DetectionStore."""
    config = AlertConfig(
        provider="ses",
        ses_region="us-east-1",
        ses_sender="test@example.com",
        recipients=["recipient@example.com"],
        retry_attempts=3,
        retry_interval_seconds=1,
    )
    mock_store = AsyncMock(spec=DetectionStore)
    service = AlertService(
        config=config,
        detection_store=mock_store,
        email_provider=None,
    )
    return service


class TestBoundingBoxOverlap:
    """Testes de propriedade para _bounding_boxes_overlap().

    **Validates: Requirements 6.7**
    """

    def setup_method(self) -> None:
        """Cria AlertService para cada teste."""
        self._service = _make_alert_service()

    @_PBT_SETTINGS
    @given(data=overlapping_bbox_pair_st())
    def test_overlapping_boxes_are_detected(
        self, data: tuple[BoundingBox, BoundingBox]
    ) -> None:
        """Bounding boxes com todas coordenadas diferindo por <= 5%
        devem ser considerados sobrepostos."""
        box1, box2 = data

        # Verifica pré-condição: todas diferenças <= tolerância
        # (clamping pode alterar, então re-verificamos)
        diffs_within = (
            abs(box1.x_percent - box2.x_percent) <= _BBOX_TOLERANCE
            and abs(box1.y_percent - box2.y_percent) <= _BBOX_TOLERANCE
            and abs(box1.width_percent - box2.width_percent) <= _BBOX_TOLERANCE
            and abs(box1.height_percent - box2.height_percent) <= _BBOX_TOLERANCE
        )

        if diffs_within:
            assert self._service._bounding_boxes_overlap(box1, box2) is True

    @_PBT_SETTINGS
    @given(data=non_overlapping_bbox_pair_st())
    def test_non_overlapping_boxes_are_not_detected(
        self, data: tuple[BoundingBox, BoundingBox]
    ) -> None:
        """Bounding boxes com alguma coordenada diferindo por > 5%
        NÃO devem ser considerados sobrepostos."""
        box1, box2 = data

        # Verifica pré-condição: pelo menos uma diferença > tolerância
        has_exceeding = (
            abs(box1.x_percent - box2.x_percent) > _BBOX_TOLERANCE
            or abs(box1.y_percent - box2.y_percent) > _BBOX_TOLERANCE
            or abs(box1.width_percent - box2.width_percent) > _BBOX_TOLERANCE
            or abs(box1.height_percent - box2.height_percent) > _BBOX_TOLERANCE
        )

        if has_exceeding:
            assert self._service._bounding_boxes_overlap(box1, box2) is False

    @_PBT_SETTINGS
    @given(box=bounding_box_st())
    def test_box_overlaps_with_itself(self, box: BoundingBox) -> None:
        """Um bounding box sempre se sobrepõe consigo mesmo (reflexividade)."""
        assert self._service._bounding_boxes_overlap(box, box) is True

    @_PBT_SETTINGS
    @given(data=overlapping_bbox_pair_st())
    def test_overlap_is_symmetric(
        self, data: tuple[BoundingBox, BoundingBox]
    ) -> None:
        """A sobreposição é simétrica: overlap(a, b) == overlap(b, a)."""
        box1, box2 = data
        result_ab = self._service._bounding_boxes_overlap(box1, box2)
        result_ba = self._service._bounding_boxes_overlap(box2, box1)
        assert result_ab == result_ba


class TestShouldSuppress:
    """Testes de propriedade para _should_suppress().

    **Validates: Requirements 6.7**
    """

    @pytest.fixture(autouse=True)
    def setup_service(self) -> None:
        """Cria AlertService com mock do DetectionStore."""
        self._service = _make_alert_service()

    @_PBT_SETTINGS
    @given(
        match_type=_match_type_st,
        bbox_pair=overlapping_bbox_pair_st(),
        target_url=_target_url_st,
    )
    async def test_same_match_type_overlapping_bbox_is_suppressed(
        self,
        match_type: str,
        bbox_pair: tuple[BoundingBox, BoundingBox],
        target_url: str,
    ) -> None:
        """Detecção com MESMO match_type E bounding boxes sobrepostos
        deve ser suprimida quando existe no ciclo anterior."""
        prev_box, curr_box = bbox_pair

        # Verifica pré-condição (clamping pode ter alterado)
        diffs_within = (
            abs(prev_box.x_percent - curr_box.x_percent) <= _BBOX_TOLERANCE
            and abs(prev_box.y_percent - curr_box.y_percent) <= _BBOX_TOLERANCE
            and abs(prev_box.width_percent - curr_box.width_percent)
            <= _BBOX_TOLERANCE
            and abs(prev_box.height_percent - curr_box.height_percent)
            <= _BBOX_TOLERANCE
        )
        if not diffs_within:
            return  # Skip este caso (clamping alterou)

        # Detecção anterior (mesmo match_type, bbox overlapping)
        prev_detection = DetectionResult(
            target_url=target_url,
            match_type=match_type,
            confidence=80,
            bounding_box=prev_box,
            description="previous detection",
            detected_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            screenshot_ref_id="screenshot-prev",
        )

        # Detecção atual
        curr_detection = DetectionResult(
            target_url=target_url,
            match_type=match_type,
            confidence=85,
            bounding_box=curr_box,
            description="current detection",
            detected_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            screenshot_ref_id="screenshot-curr",
        )

        # Mock do store retornando a detecção anterior
        self._service._detection_store.get_previous_cycle_detections = (
            AsyncMock(return_value=[prev_detection])
        )

        result = await self._service._should_suppress(curr_detection)
        assert result is True

    @_PBT_SETTINGS
    @given(
        curr_match=_match_type_st,
        bbox_pair=overlapping_bbox_pair_st(),
        target_url=_target_url_st,
    )
    async def test_different_match_type_is_never_suppressed(
        self,
        curr_match: str,
        bbox_pair: tuple[BoundingBox, BoundingBox],
        target_url: str,
    ) -> None:
        """Detecção com match_type DIFERENTE do anterior NUNCA é suprimida,
        mesmo com bounding boxes sobrepostos."""
        prev_box, curr_box = bbox_pair

        # Garante match_type diferente
        prev_match = "text" if curr_match == "logo" else "logo"

        # Detecção anterior com match_type diferente
        prev_detection = DetectionResult(
            target_url=target_url,
            match_type=prev_match,
            confidence=80,
            bounding_box=prev_box,
            description="previous detection",
            detected_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            screenshot_ref_id="screenshot-prev",
        )

        # Detecção atual
        curr_detection = DetectionResult(
            target_url=target_url,
            match_type=curr_match,
            confidence=85,
            bounding_box=curr_box,
            description="current detection",
            detected_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            screenshot_ref_id="screenshot-curr",
        )

        # Mock do store retornando a detecção anterior
        self._service._detection_store.get_previous_cycle_detections = (
            AsyncMock(return_value=[prev_detection])
        )

        result = await self._service._should_suppress(curr_detection)
        assert result is False

    @_PBT_SETTINGS
    @given(
        match_type=_match_type_st,
        bbox_pair=non_overlapping_bbox_pair_st(),
        target_url=_target_url_st,
    )
    async def test_non_overlapping_bbox_is_not_suppressed(
        self,
        match_type: str,
        bbox_pair: tuple[BoundingBox, BoundingBox],
        target_url: str,
    ) -> None:
        """Detecção com bounding boxes NÃO sobrepostos (alguma coord > 5%)
        NÃO é suprimida, mesmo com match_type igual."""
        prev_box, curr_box = bbox_pair

        # Verifica pré-condição: pelo menos uma diferença > tolerância
        has_exceeding = (
            abs(prev_box.x_percent - curr_box.x_percent) > _BBOX_TOLERANCE
            or abs(prev_box.y_percent - curr_box.y_percent) > _BBOX_TOLERANCE
            or abs(prev_box.width_percent - curr_box.width_percent)
            > _BBOX_TOLERANCE
            or abs(prev_box.height_percent - curr_box.height_percent)
            > _BBOX_TOLERANCE
        )
        if not has_exceeding:
            return  # Skip (clamping alterou)

        # Detecção anterior com match_type igual mas bbox diferente
        prev_detection = DetectionResult(
            target_url=target_url,
            match_type=match_type,
            confidence=80,
            bounding_box=prev_box,
            description="previous detection",
            detected_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            screenshot_ref_id="screenshot-prev",
        )

        # Detecção atual
        curr_detection = DetectionResult(
            target_url=target_url,
            match_type=match_type,
            confidence=85,
            bounding_box=curr_box,
            description="current detection",
            detected_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            screenshot_ref_id="screenshot-curr",
        )

        # Mock do store retornando a detecção anterior
        self._service._detection_store.get_previous_cycle_detections = (
            AsyncMock(return_value=[prev_detection])
        )

        result = await self._service._should_suppress(curr_detection)
        assert result is False

    @_PBT_SETTINGS
    @given(
        match_type=_match_type_st,
        bbox=bounding_box_st(),
        target_url=_target_url_st,
    )
    async def test_no_previous_detections_never_suppresses(
        self,
        match_type: str,
        bbox: BoundingBox,
        target_url: str,
    ) -> None:
        """Quando não há detecções anteriores, nada é suprimido."""
        curr_detection = DetectionResult(
            target_url=target_url,
            match_type=match_type,
            confidence=85,
            bounding_box=bbox,
            description="current detection",
            detected_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            screenshot_ref_id="screenshot-curr",
        )

        # Mock retornando lista vazia (sem detecções anteriores)
        self._service._detection_store.get_previous_cycle_detections = (
            AsyncMock(return_value=[])
        )

        result = await self._service._should_suppress(curr_detection)
        assert result is False
