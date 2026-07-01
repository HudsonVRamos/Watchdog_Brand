"""Property tests para Expired Item Cleanup.

**Validates: Requirements 7.4, 8.4**

Property 17: Expired Item Cleanup — itens com expiration variada,
cleanup remove apenas expirados.

Garante que o cleanup de detecções expiradas:
- Remove TODOS os itens com expires_at <= now
- Preserva TODOS os itens com expires_at > now
- Retorna a contagem correta de itens removidos
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
    DetectionResultModel,
    MonitoringCycleModel,
    ScreenshotModel,
    TargetSiteModel,
)
from brand_watchdog.storage.detection_store import DetectionStore


# Configuração do Hypothesis para testes PBT
_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)

# Retention days fixo para cálculos previsíveis
_RETENTION_DAYS = 90


@st.composite
def detection_items_strategy(draw: st.DrawFn) -> list[dict]:
    """Gera lista de itens de detecção com datas variadas.

    Cada item tem um detected_at que pode resultar em:
    - Expirado: detected_at + retention_days <= now
    - Válido: detected_at + retention_days > now

    Gera entre 1 e 15 itens para manter testes rápidos.
    """
    now = datetime.now(timezone.utc)

    # Número de itens a gerar
    n_items = draw(st.integers(min_value=1, max_value=15))

    items = []
    for _ in range(n_items):
        # Escolhe se o item será expirado ou válido
        is_expired = draw(st.booleans())

        if is_expired:
            # detected_at antigo o suficiente para que
            # detected_at + retention_days <= now
            # Ou seja, detected_at <= now - retention_days
            max_days_ago = _RETENTION_DAYS + draw(
                st.integers(min_value=1, max_value=365)
            )
            detected_at = now - timedelta(days=max_days_ago)
        else:
            # detected_at recente o suficiente para que
            # detected_at + retention_days > now
            # Ou seja, detected_at > now - retention_days
            days_ago = draw(
                st.integers(min_value=0, max_value=_RETENTION_DAYS - 1)
            )
            detected_at = now - timedelta(days=days_ago)

        items.append({
            "detected_at": detected_at,
            "is_expired": is_expired,
            "match_type": draw(st.sampled_from(["logo", "text"])),
            "confidence": draw(
                st.integers(min_value=0, max_value=100)
            ),
        })

    return items


class TestExpiredItemCleanup:
    """Property 17: Expired Item Cleanup.

    Itens com expiration variada, cleanup remove apenas expirados.

    **Validates: Requirements 7.4, 8.4**
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self):
        """Configura banco in-memory e entidades FK para cada teste."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            detection_retention_days=_RETENTION_DAYS,
        )
        setup_database(config)
        await init_db()

        # Cria TargetSiteModel para satisfazer FK
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

        # Cria MonitoringCycleModel para satisfazer FK
        self._cycle_id = str(uuid.uuid4())
        async with get_session() as session:
            cycle = MonitoringCycleModel(
                id=self._cycle_id,
                started_at=datetime.now(timezone.utc),
                status="completed",
            )
            session.add(cycle)

        # Cria ScreenshotModel para satisfazer FK
        self._screenshot_id = str(uuid.uuid4())
        async with get_session() as session:
            screenshot = ScreenshotModel(
                id=self._screenshot_id,
                target_site_id=self._target_site_id,
                monitoring_cycle_id=self._cycle_id,
                file_path="/tmp/test_screenshot.png",
                captured_at=datetime.now(timezone.utc),
                height_px=1080,
                was_truncated=False,
                expires_at=datetime.now(timezone.utc)
                + timedelta(days=365),
            )
            session.add(screenshot)

        self._store = DetectionStore(config=config)
        yield
        await close_db()

    async def _clear_detections(self) -> None:
        """Remove todas as detecções do banco entre iterações."""
        from sqlalchemy import delete

        async with get_session() as session:
            await session.execute(
                delete(DetectionResultModel)
            )

    async def _insert_detection(
        self,
        detected_at: datetime,
        match_type: str = "logo",
        confidence: int = 85,
    ) -> str:
        """Insere uma detecção diretamente no banco com expires_at calculado.

        Retorna o ID do registro criado.
        """
        detection_id = str(uuid.uuid4())
        expires_at = detected_at + timedelta(days=_RETENTION_DAYS)

        async with get_session() as session:
            model = DetectionResultModel(
                id=detection_id,
                target_site_id=self._target_site_id,
                screenshot_id=self._screenshot_id,
                monitoring_cycle_id=self._cycle_id,
                match_type=match_type,
                confidence=confidence,
                bbox_x_percent=10.0,
                bbox_y_percent=20.0,
                bbox_width_percent=30.0,
                bbox_height_percent=40.0,
                description="Detecção de teste",
                detected_at=detected_at,
                expires_at=expires_at,
            )
            session.add(model)

        return detection_id

    async def _count_all_detections(self) -> int:
        """Conta total de detecções no banco."""
        from sqlalchemy import func, select

        async with get_session() as session:
            stmt = select(func.count(DetectionResultModel.id))
            result = await session.execute(stmt)
            return result.scalar() or 0

    @_PBT_SETTINGS
    @given(items=detection_items_strategy())
    async def test_cleanup_removes_only_expired_items(
        self, items: list[dict]
    ):
        """Cleanup deve remover apenas itens com expires_at <= now
        e preservar itens com expires_at > now.

        A contagem retornada deve corresponder ao número de
        itens expirados removidos.
        """
        # Limpa dados de iterações anteriores
        await self._clear_detections()

        # Insere todos os itens no banco
        expected_expired_count = 0
        expected_valid_count = 0

        for item in items:
            await self._insert_detection(
                detected_at=item["detected_at"],
                match_type=item["match_type"],
                confidence=item["confidence"],
            )
            if item["is_expired"]:
                expected_expired_count += 1
            else:
                expected_valid_count += 1

        # Verifica que todos foram inseridos
        total_before = await self._count_all_detections()
        assert total_before == len(items), (
            f"Esperado {len(items)} inseridos, "
            f"encontrado {total_before}"
        )

        # Executa cleanup
        removed_count = await self._store.cleanup_expired()

        # Verifica contagem de removidos
        assert removed_count == expected_expired_count, (
            f"cleanup_expired() retornou {removed_count}, "
            f"esperado {expected_expired_count} expirados"
        )

        # Verifica que apenas válidos permanecem
        total_after = await self._count_all_detections()
        assert total_after == expected_valid_count, (
            f"Após cleanup, restaram {total_after} itens, "
            f"esperado {expected_valid_count} válidos"
        )

    @_PBT_SETTINGS
    @given(items=detection_items_strategy())
    async def test_valid_items_preserved_after_cleanup(
        self, items: list[dict]
    ):
        """Todos os itens com expires_at > now devem permanecer
        intactos após o cleanup — seus dados não são alterados."""
        from sqlalchemy import select

        # Limpa dados de iterações anteriores
        await self._clear_detections()

        # Insere itens e rastreia IDs dos válidos
        valid_ids: list[str] = []

        for item in items:
            det_id = await self._insert_detection(
                detected_at=item["detected_at"],
                match_type=item["match_type"],
                confidence=item["confidence"],
            )
            if not item["is_expired"]:
                valid_ids.append(det_id)

        # Executa cleanup
        await self._store.cleanup_expired()

        # Verifica que todos os IDs válidos ainda existem
        async with get_session() as session:
            stmt = select(DetectionResultModel.id).where(
                DetectionResultModel.id.in_(valid_ids)
            )
            result = await session.execute(stmt)
            remaining_ids = set(result.scalars().all())

        assert remaining_ids == set(valid_ids), (
            f"IDs válidos perdidos após cleanup: "
            f"esperado {set(valid_ids)}, "
            f"encontrado {remaining_ids}"
        )

    @_PBT_SETTINGS
    @given(items=detection_items_strategy())
    async def test_cleanup_count_matches_expired_items(
        self, items: list[dict]
    ):
        """O valor retornado por cleanup_expired() deve ser
        exatamente igual ao número de itens que tinham
        expires_at <= now no momento do cleanup."""
        # Limpa dados de iterações anteriores
        await self._clear_detections()

        # Insere itens
        for item in items:
            await self._insert_detection(
                detected_at=item["detected_at"],
                match_type=item["match_type"],
                confidence=item["confidence"],
            )

        # Conta expirados antes do cleanup
        now = datetime.now(timezone.utc)
        expected_expired = sum(
            1
            for item in items
            if (
                item["detected_at"] + timedelta(days=_RETENTION_DAYS)
                <= now
            )
        )

        # Executa cleanup
        removed_count = await self._store.cleanup_expired()

        assert removed_count == expected_expired, (
            f"cleanup retornou {removed_count}, "
            f"mas {expected_expired} itens tinham "
            f"expires_at <= now"
        )
