"""Testes unitários para o DetectionStore.

Valida persistência, consulta, paginação, cleanup de expirados
e recuperação de detecções do ciclo anterior.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from typing import Optional

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    init_db,
    setup_database,
    get_session,
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


def _make_detection(
    target_url: str = "https://example.com",
    match_type: str = "logo",
    confidence: int = 85,
    detected_at: Optional[datetime] = None,
    screenshot_ref_id: str = "screenshot-001",
) -> DetectionResult:
    """Cria uma DetectionResult para testes."""
    return DetectionResult(
        target_url=target_url,
        match_type=match_type,
        confidence=confidence,
        bounding_box=BoundingBox(
            x_percent=10.0,
            y_percent=20.0,
            width_percent=30.0,
            height_percent=40.0,
        ),
        description="Logo detectado no header",
        detected_at=detected_at or datetime.now(timezone.utc),
        screenshot_ref_id=screenshot_ref_id,
    )


@pytest.fixture(autouse=True)
async def setup_test_db():
    """Configura banco in-memory para cada teste."""
    config = StorageConfig(
        database_url="sqlite+aiosqlite:///:memory:"
    )
    setup_database(config)
    await init_db()
    yield
    await close_db()


@pytest.fixture
def storage_config() -> StorageConfig:
    """Cria configuração de storage para testes."""
    return StorageConfig(
        detection_retention_days=90,
        database_url="sqlite+aiosqlite:///:memory:",
    )


@pytest.fixture
def store(storage_config: StorageConfig) -> DetectionStore:
    """Cria instância do DetectionStore."""
    return DetectionStore(config=storage_config)


@pytest.fixture
async def target_site_id() -> str:
    """Cria um Target Site no banco e retorna o ID."""
    async with get_session() as session:
        site = TargetSiteModel(
            id="https://example.com",
            url="https://example.com",
            normalized_url="https://example.com",
            active=True,
        )
        session.add(site)
        await session.flush()
    return "https://example.com"


@pytest.fixture
async def cycle_id() -> str:
    """Cria um MonitoringCycle no banco e retorna o ID."""
    async with get_session() as session:
        cycle = MonitoringCycleModel(
            id="cycle-001",
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        session.add(cycle)
        await session.flush()
    return "cycle-001"


@pytest.fixture
async def screenshot_id(target_site_id: str, cycle_id: str) -> str:
    """Cria um Screenshot no banco e retorna o ID."""
    async with get_session() as session:
        screenshot = ScreenshotModel(
            id="screenshot-001",
            target_site_id=target_site_id,
            monitoring_cycle_id=cycle_id,
            s3_key="screenshots/cycle-001/screenshot-001.png",
            captured_at=datetime.now(timezone.utc),
            height_px=1080,
            was_truncated=False,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=90),
        )
        session.add(screenshot)
        await session.flush()
    return "screenshot-001"


class TestSave:
    """Testes para save()."""

    async def test_persiste_deteccao_com_sucesso(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve persistir detecção e retornar ID."""
        detection = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
        )

        result_id = await store.save(detection)

        assert result_id is not None
        assert len(result_id) == 36  # UUID format

    async def test_calcula_expires_at_corretamente(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve calcular expires_at = detected_at + retention_days."""
        now = datetime.now(timezone.utc)
        detection = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
            detected_at=now,
        )

        result_id = await store.save(detection)

        # Verifica no banco
        async with get_session() as session:
            from sqlalchemy import select

            stmt = select(DetectionResultModel).where(
                DetectionResultModel.id == result_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one()

        expected_expires = now + timedelta(days=90)
        # SQLite retorna datetime naive; forçamos UTC
        expires_at = model.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        diff = abs(
            (expires_at - expected_expires).total_seconds()
        )
        assert diff < 2  # tolerância de 2 segundos

    async def test_persiste_bounding_box_corretamente(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve persistir coordenadas do bounding box."""
        detection = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
        )

        result_id = await store.save(detection)

        async with get_session() as session:
            from sqlalchemy import select

            stmt = select(DetectionResultModel).where(
                DetectionResultModel.id == result_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one()

        assert model.bbox_x_percent == 10.0
        assert model.bbox_y_percent == 20.0
        assert model.bbox_width_percent == 30.0
        assert model.bbox_height_percent == 40.0

    async def test_persiste_match_type_e_confidence(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve persistir tipo e confiança corretamente."""
        detection = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
            match_type="text",
            confidence=92,
        )

        result_id = await store.save(detection)

        async with get_session() as session:
            from sqlalchemy import select

            stmt = select(DetectionResultModel).where(
                DetectionResultModel.id == result_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one()

        assert model.match_type == "text"
        assert model.confidence == 92


class TestQuery:
    """Testes para query()."""

    async def test_retorna_resultados_em_ordem_reversa(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve retornar resultados mais recentes primeiro."""
        base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        for i in range(3):
            detection = _make_detection(
                target_url=target_site_id,
                screenshot_ref_id=screenshot_id,
                detected_at=base_time + timedelta(hours=i),
            )
            await store.save(detection)

        result = await store.query()

        assert len(result.results) == 3
        # Primeiro resultado deve ser o mais recente
        assert result.results[0].detected_at > (
            result.results[1].detected_at
        )
        assert result.results[1].detected_at > (
            result.results[2].detected_at
        )

    async def test_filtra_por_target_url(
        self,
        store: DetectionStore,
        screenshot_id: str,
    ):
        """Deve filtrar por target_url."""
        # Cria segundo target site
        async with get_session() as session:
            site2 = TargetSiteModel(
                id="https://other.com",
                url="https://other.com",
                normalized_url="https://other.com",
                active=True,
            )
            session.add(site2)
            await session.flush()

        d1 = _make_detection(
            target_url="https://example.com",
            screenshot_ref_id=screenshot_id,
        )
        d2 = _make_detection(
            target_url="https://other.com",
            screenshot_ref_id=screenshot_id,
        )
        await store.save(d1)
        await store.save(d2)

        result = await store.query(
            target_url="https://example.com"
        )

        assert result.total_count == 1
        assert result.results[0].target_url == (
            "https://example.com"
        )

    async def test_filtra_por_match_type(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve filtrar por match_type."""
        d1 = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
            match_type="logo",
        )
        d2 = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
            match_type="text",
        )
        await store.save(d1)
        await store.save(d2)

        result = await store.query(match_type="text")

        assert result.total_count == 1
        assert result.results[0].match_type == "text"

    async def test_filtra_por_date_range(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve filtrar por intervalo de datas."""
        base = datetime(2024, 6, 1, tzinfo=timezone.utc)
        d1 = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
            detected_at=base,
        )
        d2 = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
            detected_at=base + timedelta(days=5),
        )
        await store.save(d1)
        await store.save(d2)

        result = await store.query(
            start_date=base + timedelta(days=1),
            end_date=base + timedelta(days=10),
        )

        assert result.total_count == 1

    async def test_paginacao_funciona(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve paginar resultados corretamente."""
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            d = _make_detection(
                target_url=target_site_id,
                screenshot_ref_id=screenshot_id,
                detected_at=base + timedelta(hours=i),
            )
            await store.save(d)

        result = await store.query(page=1, page_size=2)

        assert len(result.results) == 2
        assert result.total_count == 5
        assert result.page == 1
        assert result.page_size == 2
        assert result.has_next is True

    async def test_page_size_limitado_a_100(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve limitar page_size a 100."""
        d = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
        )
        await store.save(d)

        result = await store.query(page_size=500)

        assert result.page_size == 100

    async def test_retorna_vazio_sem_resultados(
        self,
        store: DetectionStore,
    ):
        """Deve retornar QueryResult vazio se não houver resultados."""
        result = await store.query(target_url="https://nada.com")

        assert result.results == []
        assert result.total_count == 0
        assert result.has_next is False

    async def test_has_next_false_na_ultima_pagina(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve retornar has_next=False na última página."""
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for i in range(3):
            d = _make_detection(
                target_url=target_site_id,
                screenshot_ref_id=screenshot_id,
                detected_at=base + timedelta(hours=i),
            )
            await store.save(d)

        result = await store.query(page=2, page_size=2)

        assert len(result.results) == 1
        assert result.has_next is False


class TestCleanupExpired:
    """Testes para cleanup_expired()."""

    async def test_remove_deteccoes_expiradas(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve remover detecções com expires_at no passado."""
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        d = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
            detected_at=past,
        )
        await store.save(d)

        removed = await store.cleanup_expired()

        assert removed == 1

    async def test_nao_remove_deteccoes_validas(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Não deve remover detecções que ainda não expiraram."""
        d = _make_detection(
            target_url=target_site_id,
            screenshot_ref_id=screenshot_id,
        )
        await store.save(d)

        removed = await store.cleanup_expired()

        assert removed == 0

    async def test_retorna_zero_sem_expirados(
        self,
        store: DetectionStore,
    ):
        """Deve retornar 0 quando não há detecções expiradas."""
        removed = await store.cleanup_expired()
        assert removed == 0


class TestGetPreviousCycleDetections:
    """Testes para get_previous_cycle_detections()."""

    async def test_retorna_deteccoes_do_ciclo_anterior(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve retornar detecções do ciclo anterior."""
        # Cria segundo ciclo
        async with get_session() as session:
            cycle2 = MonitoringCycleModel(
                id="cycle-002",
                started_at=datetime.now(timezone.utc),
                status="completed",
            )
            session.add(cycle2)
            await session.flush()

        # Cria screenshot para ciclo 2
        async with get_session() as session:
            ss2 = ScreenshotModel(
                id="screenshot-002",
                target_site_id=target_site_id,
                monitoring_cycle_id="cycle-002",
                s3_key="screenshots/cycle-002/screenshot-002.png",
                captured_at=datetime.now(timezone.utc),
                height_px=1080,
                was_truncated=False,
                expires_at=datetime.now(timezone.utc)
                + timedelta(days=90),
            )
            session.add(ss2)
            await session.flush()

        # Detecção no ciclo 1 (anterior)
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        async with get_session() as session:
            d1 = DetectionResultModel(
                id="det-001",
                target_site_id=target_site_id,
                screenshot_id=screenshot_id,
                monitoring_cycle_id="cycle-001",
                match_type="logo",
                confidence=85,
                bbox_x_percent=10.0,
                bbox_y_percent=20.0,
                bbox_width_percent=30.0,
                bbox_height_percent=40.0,
                description="Logo no header",
                detected_at=base,
                expires_at=base + timedelta(days=90),
            )
            session.add(d1)
            await session.flush()

        # Detecção no ciclo 2 (mais recente)
        async with get_session() as session:
            d2 = DetectionResultModel(
                id="det-002",
                target_site_id=target_site_id,
                screenshot_id="screenshot-002",
                monitoring_cycle_id="cycle-002",
                match_type="logo",
                confidence=90,
                bbox_x_percent=10.0,
                bbox_y_percent=20.0,
                bbox_width_percent=30.0,
                bbox_height_percent=40.0,
                description="Logo no header",
                detected_at=base + timedelta(hours=24),
                expires_at=base + timedelta(days=90),
            )
            session.add(d2)
            await session.flush()

        results = await store.get_previous_cycle_detections(
            target_site_id
        )

        assert len(results) == 1
        assert results[0].confidence == 85

    async def test_retorna_vazio_sem_ciclo_anterior(
        self,
        store: DetectionStore,
        target_site_id: str,
        screenshot_id: str,
    ):
        """Deve retornar lista vazia se há apenas um ciclo."""
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        async with get_session() as session:
            d = DetectionResultModel(
                id="det-001",
                target_site_id=target_site_id,
                screenshot_id=screenshot_id,
                monitoring_cycle_id="cycle-001",
                match_type="logo",
                confidence=85,
                bbox_x_percent=10.0,
                bbox_y_percent=20.0,
                bbox_width_percent=30.0,
                bbox_height_percent=40.0,
                description="Logo no header",
                detected_at=base,
                expires_at=base + timedelta(days=90),
            )
            session.add(d)
            await session.flush()

        results = await store.get_previous_cycle_detections(
            target_site_id
        )

        assert results == []

    async def test_retorna_vazio_sem_deteccoes(
        self,
        store: DetectionStore,
    ):
        """Deve retornar lista vazia sem detecções para o target."""
        results = await store.get_previous_cycle_detections(
            "https://nada.com"
        )
        assert results == []
