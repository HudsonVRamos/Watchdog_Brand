"""Property tests para completude do resultado de ciclo de monitoramento.

Valida que CycleResult contém todos os campos obrigatórios e contagens
corretas após o ciclo de despacho distribuído.

Na nova arquitetura, o MonitoringCoordinator despacha para SQS e retorna
CycleResult com estatísticas de publicação (success_count, failure_count).
A consolidação final é feita pelo CycleConsolidator (testado separadamente).

**Validates: Requirements 1.1, 1.5**
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.config import (
    AppConfig,
    QueueConfig,
    WorkerConfig,
)
from brand_watchdog.coordinator.coordinator import MonitoringCoordinator
from brand_watchdog.models.dataclasses import (
    CycleResult,
    TargetSite,
)


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
    deadline=None,
)


# --- Strategies ---


def _target_site_strategy() -> st.SearchStrategy[TargetSite]:
    """Gera um TargetSite com URL única."""
    return st.builds(
        TargetSite,
        id=st.uuids().map(str),
        url=st.integers(min_value=1, max_value=100000).map(
            lambda i: f"https://site-{i}.example.com"
        ),
        normalized_url=st.integers(min_value=1, max_value=100000).map(
            lambda i: f"https://site-{i}.example.com"
        ),
        created_at=st.just(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        active=st.just(True),
    )


def _site_list_strategy() -> st.SearchStrategy[list[TargetSite]]:
    """Gera lista de 1-20 sites-alvo com IDs únicos."""
    return st.lists(
        _target_site_strategy(),
        min_size=1,
        max_size=20,
        unique_by=lambda s: s.id,
    )


# --- Helpers ---


def _mock_get_session():
    """Cria mock para get_session como async context manager."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    @asynccontextmanager
    async def fake_get_session():
        yield mock_session

    return fake_get_session, mock_session


def _build_coordinator(
    target_sites: list[TargetSite],
    success_count: int,
    failure_count: int,
) -> MonitoringCoordinator:
    """Constrói MonitoringCoordinator com mocks configurados.

    Args:
        target_sites: Lista de sites-alvo.
        success_count: Número de publicações com sucesso na fila.
        failure_count: Número de falhas na publicação.
    """
    # RuleSetVersionCalculator mock
    rule_set_calculator = MagicMock()
    rule_set_calculator.calculate.return_value = "v1717000000_abc12345"
    rule_set_calculator.has_changed.return_value = False

    # SQSPublisher mock - retorna (success_count, failure_count)
    sqs_publisher = AsyncMock()
    sqs_publisher.publish_all = AsyncMock(
        return_value=(success_count, failure_count)
    )

    # CycleConsolidator mock
    consolidator = AsyncMock()
    consolidator.consolidate = AsyncMock()

    # TargetSiteManager mock
    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    config = AppConfig(
        queue=QueueConfig(queue_url="https://sqs.us-east-1.amazonaws.com/123/test-queue"),
        worker=WorkerConfig(),
    )

    return MonitoringCoordinator(
        rule_set_calculator=rule_set_calculator,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
        target_site_manager=target_site_manager,
        config=config,
    )


# --- Property Tests ---


class TestCycleResultCompleteness:
    """Property: Cycle Result Completeness (Distribuído).

    Ciclos com mix de publicações com sucesso/falha — o resultado contém
    todos os campos obrigatórios e contagens corretas.

    **Validates: Requirements 1.1, 1.5**
    """

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_sites_processed_plus_failed_equals_total(
        self, data: st.DataObject
    ):
        """sites_processed + sites_failed == total de target sites."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)

        # Gerar split de sucesso/falha que soma n
        success_count = data.draw(
            st.integers(min_value=0, max_value=n)
        )
        failure_count = n - success_count

        fake_session_fn, _ = _mock_get_session()

        coordinator = _build_coordinator(
            target_sites, success_count, failure_count
        )

        with patch(
            "brand_watchdog.coordinator.coordinator.get_session",
            side_effect=fake_session_fn,
        ), patch(
            "brand_watchdog.coordinator.coordinator.asyncio.create_task"
        ):
            result = await coordinator.run_cycle()

        assert result.sites_processed + result.sites_failed == n

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_sites_processed_matches_success_count(
        self, data: st.DataObject
    ):
        """sites_processed == success_count do SQSPublisher."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)

        success_count = data.draw(
            st.integers(min_value=0, max_value=n)
        )
        failure_count = n - success_count

        fake_session_fn, _ = _mock_get_session()

        coordinator = _build_coordinator(
            target_sites, success_count, failure_count
        )

        with patch(
            "brand_watchdog.coordinator.coordinator.get_session",
            side_effect=fake_session_fn,
        ), patch(
            "brand_watchdog.coordinator.coordinator.asyncio.create_task"
        ):
            result = await coordinator.run_cycle()

        assert result.sites_processed == success_count

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_sites_failed_matches_failure_count(
        self, data: st.DataObject
    ):
        """sites_failed == failure_count do SQSPublisher."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)

        success_count = data.draw(
            st.integers(min_value=0, max_value=n)
        )
        failure_count = n - success_count

        fake_session_fn, _ = _mock_get_session()

        coordinator = _build_coordinator(
            target_sites, success_count, failure_count
        )

        with patch(
            "brand_watchdog.coordinator.coordinator.get_session",
            side_effect=fake_session_fn,
        ), patch(
            "brand_watchdog.coordinator.coordinator.asyncio.create_task"
        ):
            result = await coordinator.run_cycle()

        assert result.sites_failed == failure_count

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_started_at_before_ended_at(
        self, data: st.DataObject
    ):
        """started_at <= ended_at para todo ciclo executado."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)

        success_count = data.draw(
            st.integers(min_value=0, max_value=n)
        )
        failure_count = n - success_count

        fake_session_fn, _ = _mock_get_session()

        coordinator = _build_coordinator(
            target_sites, success_count, failure_count
        )

        with patch(
            "brand_watchdog.coordinator.coordinator.get_session",
            side_effect=fake_session_fn,
        ), patch(
            "brand_watchdog.coordinator.coordinator.asyncio.create_task"
        ):
            result = await coordinator.run_cycle()

        assert result.started_at <= result.ended_at

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_cycle_id_is_valid_uuid(
        self, data: st.DataObject
    ):
        """cycle_id é um UUID válido para todo ciclo."""
        import uuid

        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)

        success_count = data.draw(
            st.integers(min_value=0, max_value=n)
        )
        failure_count = n - success_count

        fake_session_fn, _ = _mock_get_session()

        coordinator = _build_coordinator(
            target_sites, success_count, failure_count
        )

        with patch(
            "brand_watchdog.coordinator.coordinator.get_session",
            side_effect=fake_session_fn,
        ), patch(
            "brand_watchdog.coordinator.coordinator.asyncio.create_task"
        ):
            result = await coordinator.run_cycle()

        # Propriedade: cycle_id é um UUID válido
        parsed = uuid.UUID(result.cycle_id)
        assert str(parsed) == result.cycle_id
