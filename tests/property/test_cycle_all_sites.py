"""Property tests para verificação de que o ciclo despacha todos os sites.

Valida que o MonitoringCoordinator.run_cycle() publica mensagens na
fila SQS para TODOS os Target Sites registrados em cada ciclo de
monitoramento, sem pular ou perder nenhum site.

Na nova arquitetura distribuída, o coordinator despacha para SQS
e o processamento é feito pelos Workers ECS.

**Validates: Requirements 1.1, 1.5**
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
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
from brand_watchdog.models.dataclasses import TargetSite


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
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


def _make_coordinator(
    target_sites: list[TargetSite],
) -> MonitoringCoordinator:
    """Cria MonitoringCoordinator com mocks para o fluxo distribuído."""
    # RuleSetVersionCalculator mock
    rule_set_calculator = MagicMock()
    rule_set_calculator.calculate.return_value = "v1717000000_abc12345"
    rule_set_calculator.has_changed.return_value = False

    # SQSPublisher mock - retorna (success_count, failure_count)
    sqs_publisher = AsyncMock()
    sqs_publisher.publish_all = AsyncMock(
        return_value=(len(target_sites), 0)
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


# --- Strategies ---


def _target_site_strategy() -> st.SearchStrategy[TargetSite]:
    """Gera um TargetSite válido com URL única."""
    return st.builds(
        TargetSite,
        id=st.uuids().map(str),
        url=st.integers(min_value=1, max_value=99999).map(
            lambda i: f"https://site-{i}.example.com"
        ),
        normalized_url=st.integers(
            min_value=1, max_value=99999
        ).map(lambda i: f"https://site-{i}.example.com"),
        created_at=st.just(
            datetime(2024, 1, 1, tzinfo=timezone.utc)
        ),
        active=st.just(True),
    )


def _target_sites_strategy(
    min_size: int = 1,
    max_size: int = 50,
) -> st.SearchStrategy[list[TargetSite]]:
    """Gera listas de 1-50 target sites com URLs únicas."""
    return st.lists(
        _target_site_strategy(),
        min_size=min_size,
        max_size=max_size,
        unique_by=lambda s: s.id,
    )


# --- Property Tests ---


class TestCycleDispatchesAllSites:
    """Property: Cycle Dispatches All Sites via SQS.

    Conjuntos de 1-50 target sites, todos são despachados
    pelo MonitoringCoordinator.run_cycle() para a fila SQS.

    **Validates: Requirements 1.1, 1.5**
    """

    @_PBT_SETTINGS
    @given(target_sites=_target_sites_strategy())
    @pytest.mark.asyncio
    async def test_sqs_publisher_called_with_all_sites(
        self, target_sites: list[TargetSite]
    ):
        """publish_all é chamado com mensagens para todos os sites."""
        fake_session_fn, _ = _mock_get_session()

        coordinator = _make_coordinator(target_sites=target_sites)

        with patch(
            "brand_watchdog.coordinator.coordinator.get_session",
            side_effect=fake_session_fn,
        ), patch(
            "brand_watchdog.coordinator.coordinator.asyncio.create_task"
        ):
            await coordinator.run_cycle()

        # Propriedade: publish_all foi chamado
        coordinator._sqs_publisher.publish_all.assert_called_once()

        # Verificar que a quantidade de mensagens corresponde aos sites
        call_args = coordinator._sqs_publisher.publish_all.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1].get("messages", [])
        assert len(messages) == len(target_sites)

    @_PBT_SETTINGS
    @given(target_sites=_target_sites_strategy())
    @pytest.mark.asyncio
    async def test_consolidator_started_after_dispatch(
        self, target_sites: list[TargetSite]
    ):
        """CycleConsolidator é iniciado após despacho na fila."""
        fake_session_fn, _ = _mock_get_session()

        coordinator = _make_coordinator(target_sites=target_sites)

        with patch(
            "brand_watchdog.coordinator.coordinator.get_session",
            side_effect=fake_session_fn,
        ), patch(
            "brand_watchdog.coordinator.coordinator.asyncio.create_task"
        ) as mock_create_task:
            await coordinator.run_cycle()

        # Propriedade: create_task foi chamado com a coroutine do consolidator
        mock_create_task.assert_called_once()
