"""Property tests para Detection Result Persistence Round-Trip.

**Validates: Requirements 7.1**

Property 15: Detection Result Persistence Round-Trip —
DetectionResults completos, save + query produz dados idênticos.

Garante que a persistência de detecções é sem perda:
salvar um DetectionResult e consultá-lo de volta produz
exatamente os mesmos dados (target_url, match_type, confidence,
bounding_box, description, detected_at).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from sqlalchemy import select

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    get_session,
    init_db,
    setup_database,
)
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    DetectionResult,
)
from brand_watchdog.models.entities import (
    DetectionResultModel,
    MonitoringCycleModel,
    ScreenshotModel,
    TargetSiteModel,
)
from brand_watchdog.storage.detection_store import DetectionStore


_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# Strategies para geração de dados aleatórios válidos
_match_type_st = st.sampled_from(["logo", "text"])
_confidence_st = st.integers(min_value=0, max_value=100)
_bbox_coord_st = st.floats(
    min_value=0.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
)
_description_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=200,
)


@st.composite
def detection_result_st(draw: st.DrawFn) -> DetectionResult:
    """Strategy que gera DetectionResults aleatórios válidos."""
    return DetectionResult(
        target_url="https://example.com",
        match_type=draw(_match_type_st),
        confidence=draw(_confidence_st),
        bounding_box=BoundingBox(
            x_percent=draw(_bbox_coord_st),
            y_percent=draw(_bbox_coord_st),
            width_percent=draw(_bbox_coord_st),
            height_percent=draw(_bbox_coord_st),
        ),
        description=draw(_description_st),
        detected_at=draw(
            st.datetimes(
                min_value=datetime(2020, 1, 1),
                max_value=datetime(2030, 12, 31),
                timezones=st.just(timezone.utc),
            )
        ),
        screenshot_ref_id="screenshot-001",
    )


class TestDetectionPersistenceRoundTrip:
    """Property 15: Detection Result Persistence Round-Trip.

    DetectionResults completos, save + query produz dados idênticos.

    **Validates: Requirements 7.1**
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self):
        """Configura banco in-memory e fixtures de FK para cada teste."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            detection_retention_days=90,
        )
        setup_database(config)
        await init_db()

        # Criar TargetSiteModel para satisfazer FK
        async with get_session() as session:
            site = TargetSiteModel(
                id="https://example.com",
                url="https://example.com",
                normalized_url="https://example.com",
                created_at=datetime.now(timezone.utc),
                active=True,
            )
            session.add(site)

        # Criar MonitoringCycleModel para satisfazer FK
        async with get_session() as session:
            cycle = MonitoringCycleModel(
                id="cycle-001",
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            session.add(cycle)

        # Criar ScreenshotModel para satisfazer FK
        async with get_session() as session:
            screenshot = ScreenshotModel(
                id="screenshot-001",
                target_site_id="https://example.com",
                monitoring_cycle_id="cycle-001",
                file_path="/tmp/test.png",
                captured_at=datetime.now(timezone.utc),
                height_px=1080,
                was_truncated=False,
                expires_at=datetime.now(timezone.utc)
                + timedelta(days=90),
            )
            session.add(screenshot)

        self._config = config
        self._store = DetectionStore(config=config)
        yield
        await close_db()

    @_PBT_SETTINGS
    @given(detection=detection_result_st())
    async def test_save_query_produces_identical_data(
        self, detection: DetectionResult
    ):
        """Salvar um DetectionResult e consultar de volta deve
        produzir dados idênticos nos campos essenciais."""
        # Salvar detecção — retorna o ID gerado
        result_id = await self._store.save(detection)
        assert result_id is not None

        # Consultar diretamente pelo ID no banco para round-trip
        async with get_session() as session:
            stmt = select(DetectionResultModel).where(
                DetectionResultModel.id == result_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one()

        # Converter modelo para dataclass usando o mesmo
        # método que query() usa internamente
        queried = DetectionStore._model_to_dataclass(model)

        # Verificar target_url
        assert queried.target_url == detection.target_url, (
            f"target_url diverge: {queried.target_url!r} "
            f"!= {detection.target_url!r}"
        )

        # Verificar match_type
        assert queried.match_type == detection.match_type, (
            f"match_type diverge: {queried.match_type!r} "
            f"!= {detection.match_type!r}"
        )

        # Verificar confidence
        assert queried.confidence == detection.confidence, (
            f"confidence diverge: {queried.confidence} "
            f"!= {detection.confidence}"
        )

        # Verificar description
        assert queried.description == detection.description, (
            f"description diverge: {queried.description!r} "
            f"!= {detection.description!r}"
        )

        # Verificar bounding box com tolerância para floats
        _assert_float_equal(
            queried.bounding_box.x_percent,
            detection.bounding_box.x_percent,
            "bbox_x_percent",
        )
        _assert_float_equal(
            queried.bounding_box.y_percent,
            detection.bounding_box.y_percent,
            "bbox_y_percent",
        )
        _assert_float_equal(
            queried.bounding_box.width_percent,
            detection.bounding_box.width_percent,
            "bbox_width_percent",
        )
        _assert_float_equal(
            queried.bounding_box.height_percent,
            detection.bounding_box.height_percent,
            "bbox_height_percent",
        )

        # Verificar detected_at (SQLite pode perder microssegundos)
        expected_dt = detection.detected_at
        actual_dt = queried.detected_at
        if actual_dt.tzinfo is None:
            actual_dt = actual_dt.replace(tzinfo=timezone.utc)
        diff_seconds = abs(
            (actual_dt - expected_dt).total_seconds()
        )
        assert diff_seconds <= 1, (
            f"detected_at diverge: {actual_dt} != {expected_dt} "
            f"(diff={diff_seconds}s)"
        )

        # Verificar screenshot_ref_id
        assert queried.screenshot_ref_id == (
            detection.screenshot_ref_id
        ), (
            f"screenshot_ref_id diverge: "
            f"{queried.screenshot_ref_id!r} "
            f"!= {detection.screenshot_ref_id!r}"
        )


def _assert_float_equal(
    actual: float,
    expected: float,
    field_name: str,
    tolerance: float = 1e-5,
) -> None:
    """Verifica igualdade de floats com tolerância para arredondamento."""
    assert abs(actual - expected) <= tolerance, (
        f"{field_name} diverge: {actual} != {expected} "
        f"(diff={abs(actual - expected)})"
    )
