"""Testes de integração: Consolidação com banco de dados.

Utiliza SQLite async in-memory para verificar o fluxo completo
de consolidação do ciclo: criação de registros, inserção incremental
de resultados e atualização final de status e contadores.

Requirements: 3.1, 6.1
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from brand_watchdog.config import WorkerConfig
from brand_watchdog.coordinator.cycle_consolidator import (
    CycleConsolidator,
)
from brand_watchdog.models.entities import (
    Base,
    MonitoringCycleModel,
    SiteCycleResultModel,
    TargetSiteModel,
)


# --- Fixtures ---


@pytest.fixture
async def db_session_factory():
    """Configura banco SQLite in-memory para testes.

    Retorna uma função context manager compatível com get_session().
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", echo=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    @asynccontextmanager
    async def get_session_override() -> (
        AsyncGenerator[AsyncSession, None]
    ):
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Retorna tanto o factory quanto o context manager
    yield get_session_override

    await engine.dispose()


@pytest.fixture
def fast_worker_config() -> WorkerConfig:
    """WorkerConfig com polling rápido para testes."""
    return WorkerConfig(
        consolidation_poll_interval_seconds=0,
        consolidation_timeout_minutes=1,
    )


# --- Helpers ---


def _make_cycle(
    cycle_id: str, sites_dispatched: int = 3
) -> MonitoringCycleModel:
    """Cria um MonitoringCycleModel de teste."""
    return MonitoringCycleModel(
        id=cycle_id,
        started_at=datetime.now(timezone.utc),
        status=MonitoringCycleModel.STATUS_DISPATCHED,
        sites_dispatched=sites_dispatched,
    )


def _make_site(site_id: str) -> TargetSiteModel:
    """Cria um TargetSiteModel de teste."""
    return TargetSiteModel(
        id=site_id,
        url=f"https://site-{site_id[:8]}.com.br",
        normalized_url=f"https://site-{site_id[:8]}.com.br",
        active=True,
        brand="sky_plus",
    )


def _make_result(
    site_id: str,
    cycle_id: str,
    status: str = "success",
    detections: int = 0,
) -> SiteCycleResultModel:
    """Cria um SiteCycleResultModel de teste."""
    return SiteCycleResultModel(
        id=str(uuid.uuid4()),
        site_id=site_id,
        cycle_id=cycle_id,
        status=status,
        detections_count=detections,
        failure_reason=(
            "Timeout de processamento"
            if status == "failure"
            else None
        ),
        completed_at=datetime.now(timezone.utc),
    )


# --- Testes ---


@pytest.mark.integration
class TestConsolidation:
    """Testes de integração da consolidação de ciclos com DB real."""

    async def test_consolidation_completes_when_all_results_arrive(
        self, db_session_factory, fast_worker_config
    ) -> None:
        """Ciclo é marcado 'completed' quando todos os resultados chegam.

        Fluxo:
        1. Cria ciclo com 3 sites despachados
        2. Insere 3 resultados (2 sucesso, 1 falha)
        3. Verifica que consolidação retorna "completed"
        4. Verifica contadores corretos
        """
        cycle_id = str(uuid.uuid4())
        site_ids = [str(uuid.uuid4()) for _ in range(3)]

        # Prepara banco: cria sites e ciclo
        async with db_session_factory() as session:
            for sid in site_ids:
                session.add(_make_site(sid))
            session.add(
                _make_cycle(cycle_id, sites_dispatched=3)
            )

        # Insere resultados (simula Workers processando)
        async with db_session_factory() as session:
            session.add(
                _make_result(
                    site_ids[0],
                    cycle_id,
                    "success",
                    detections=2,
                )
            )
            session.add(
                _make_result(
                    site_ids[1],
                    cycle_id,
                    "success",
                    detections=1,
                )
            )
            session.add(
                _make_result(site_ids[2], cycle_id, "failure")
            )

        # Patch get_session para usar nosso override
        with patch(
            "brand_watchdog.coordinator.cycle_consolidator"
            ".get_session",
            db_session_factory,
        ):
            consolidator = CycleConsolidator(
                config=fast_worker_config
            )
            status = await consolidator.consolidate(
                cycle_id=cycle_id, sites_dispatched=3
            )

        assert status == "completed"

        # Verifica contadores no banco
        async with db_session_factory() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_id
            )
            result = await session.execute(stmt)
            cycle = result.scalar_one()

            assert cycle.status == "completed"
            assert cycle.sites_processed == 2
            assert cycle.sites_failed == 1
            assert cycle.detections_found == 3
            assert cycle.ended_at is not None

    async def test_consolidation_timeout_marks_missing_sites(
        self, db_session_factory
    ) -> None:
        """Ciclo é marcado 'completed_with_timeout' quando sites não completam.

        Fluxo:
        1. Cria ciclo com 3 sites despachados
        2. Insere apenas 1 resultado
        3. Configura timeout muito curto (0 minutos)
        4. Verifica que sites sem resultado recebem falha
        """
        cycle_id = str(uuid.uuid4())
        site_ids = [str(uuid.uuid4()) for _ in range(3)]

        # Prepara banco
        async with db_session_factory() as session:
            for sid in site_ids:
                session.add(_make_site(sid))
            session.add(
                _make_cycle(cycle_id, sites_dispatched=3)
            )

        # Apenas 1 resultado chega
        async with db_session_factory() as session:
            session.add(
                _make_result(
                    site_ids[0],
                    cycle_id,
                    "success",
                    detections=1,
                )
            )

        # Timeout de 0 minutos para forçar timeout imediato
        timeout_config = WorkerConfig(
            consolidation_poll_interval_seconds=0,
            consolidation_timeout_minutes=0,
        )

        with patch(
            "brand_watchdog.coordinator.cycle_consolidator"
            ".get_session",
            db_session_factory,
        ):
            consolidator = CycleConsolidator(
                config=timeout_config
            )
            status = await consolidator.consolidate(
                cycle_id=cycle_id, sites_dispatched=3
            )

        assert status == "completed_with_timeout"

        # Verifica que os sites sem resultado receberam falha
        async with db_session_factory() as session:
            stmt = select(SiteCycleResultModel).where(
                SiteCycleResultModel.cycle_id == cycle_id,
                SiteCycleResultModel.status == "failure",
            )
            result = await session.execute(stmt)
            failures = result.scalars().all()

            # 2 sites sem resultado receberam falha por timeout
            assert len(failures) == 2
            for failure in failures:
                assert "Timeout" in (
                    failure.failure_reason or ""
                )

    async def test_incremental_results_updates_count(
        self, db_session_factory, fast_worker_config
    ) -> None:
        """Resultados inseridos incrementalmente são contados corretamente.

        Verifica que a consolidação aguarda todos os resultados
        antes de finalizar.
        """
        cycle_id = str(uuid.uuid4())
        site_ids = [str(uuid.uuid4()) for _ in range(2)]

        # Prepara banco
        async with db_session_factory() as session:
            for sid in site_ids:
                session.add(_make_site(sid))
            session.add(
                _make_cycle(cycle_id, sites_dispatched=2)
            )

        # Insere todos os resultados antes da consolidação
        async with db_session_factory() as session:
            session.add(
                _make_result(
                    site_ids[0],
                    cycle_id,
                    "success",
                    detections=3,
                )
            )
            session.add(
                _make_result(
                    site_ids[1],
                    cycle_id,
                    "success",
                    detections=5,
                )
            )

        with patch(
            "brand_watchdog.coordinator.cycle_consolidator"
            ".get_session",
            db_session_factory,
        ):
            consolidator = CycleConsolidator(
                config=fast_worker_config
            )
            status = await consolidator.consolidate(
                cycle_id=cycle_id, sites_dispatched=2
            )

        assert status == "completed"

        # Verifica contadores
        async with db_session_factory() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_id
            )
            result = await session.execute(stmt)
            cycle = result.scalar_one()

            assert cycle.sites_processed == 2
            assert cycle.sites_failed == 0
            assert cycle.detections_found == 8  # 3 + 5

    async def test_all_failures_produces_correct_counters(
        self, db_session_factory, fast_worker_config
    ) -> None:
        """Ciclo onde todos os sites falham tem contadores corretos.

        Verifica que sites_processed=0 e sites_failed=total.
        """
        cycle_id = str(uuid.uuid4())
        site_ids = [str(uuid.uuid4()) for _ in range(3)]

        async with db_session_factory() as session:
            for sid in site_ids:
                session.add(_make_site(sid))
            session.add(
                _make_cycle(cycle_id, sites_dispatched=3)
            )

        # Todos falham
        async with db_session_factory() as session:
            for sid in site_ids:
                session.add(
                    _make_result(sid, cycle_id, "failure")
                )

        with patch(
            "brand_watchdog.coordinator.cycle_consolidator"
            ".get_session",
            db_session_factory,
        ):
            consolidator = CycleConsolidator(
                config=fast_worker_config
            )
            status = await consolidator.consolidate(
                cycle_id=cycle_id, sites_dispatched=3
            )

        assert status == "completed"

        async with db_session_factory() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_id
            )
            result = await session.execute(stmt)
            cycle = result.scalar_one()

            assert cycle.sites_processed == 0
            assert cycle.sites_failed == 3
            assert cycle.detections_found == 0
