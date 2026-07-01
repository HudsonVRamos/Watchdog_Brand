"""Property tests para Screenshot Storage Round-Trip.

**Validates: Requirements 8.1, 8.5**

Property 19: Screenshot Storage Round-Trip — PNG bytes aleatórios,
store + retrieve produz bytes idênticos.

Garante que o armazenamento de screenshots é sem perda:
ler um screenshot armazenado produz exatamente os mesmos bytes
que foram gravados.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    get_session,
    init_db,
    setup_database,
)
from brand_watchdog.models.entities import (
    MonitoringCycleModel,
    TargetSiteModel,
)
from brand_watchdog.storage.screenshot_store import ScreenshotStore


_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


class TestScreenshotStorageRoundTrip:
    """Property 19: Screenshot Storage Round-Trip.

    PNG bytes aleatórios, store + retrieve produz bytes idênticos.

    **Validates: Requirements 8.1, 8.5**
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self, tmp_path: Path):
        """Configura banco in-memory e fixtures de FK para cada teste."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            screenshot_base_path=tmp_path / "screenshots",
        )
        setup_database(config)
        await init_db()

        # Criar TargetSiteModel para satisfazer FK
        self._target_site_id = str(uuid.uuid4())
        async with get_session() as session:
            site = TargetSiteModel(
                id=self._target_site_id,
                url="https://example.com",
                normalized_url="https://example.com",
                created_at=datetime.now(timezone.utc),
                active=True,
            )
            session.add(site)

        # Criar MonitoringCycleModel para satisfazer FK
        self._cycle_id = str(uuid.uuid4())
        async with get_session() as session:
            cycle = MonitoringCycleModel(
                id=self._cycle_id,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            session.add(cycle)

        self._store = ScreenshotStore(config)
        yield
        await close_db()

    @_PBT_SETTINGS
    @given(png_bytes=st.binary(min_size=1, max_size=10000))
    async def test_store_retrieve_produces_identical_bytes(
        self, png_bytes: bytes
    ):
        """Bytes armazenados via store() e recuperados via retrieve()
        devem ser idênticos aos bytes originais."""
        # Armazenar screenshot
        screenshot_model = await self._store.store(
            png_bytes=png_bytes,
            target_site_id=self._target_site_id,
            cycle_id=self._cycle_id,
            height_px=1024,
            was_truncated=False,
        )

        # Recuperar screenshot pelo ID
        retrieved_bytes = await self._store.retrieve(screenshot_model.id)

        # Verificar identidade byte-a-byte
        assert retrieved_bytes == png_bytes, (
            f"Bytes recuperados diferem dos originais. "
            f"Original: {len(png_bytes)} bytes, "
            f"Recuperado: {len(retrieved_bytes)} bytes"
        )
