"""Testes unitários para o MonitoringCoordinator (distribuído).

Valida:
- Ciclo completo com despacho via SQS
- Lock de ciclo: pula execução se ciclo anterior em andamento
- Concurrent cycle skip: dois ciclos simultâneos, segundo é pulado
- Integração com RuleSetVersionCalculator
- Integração com SQSPublisher
- Integração com CycleConsolidator
- Registro e atualização de MonitoringCycleModel no banco
- Aborto do ciclo quando diretório de regras está vazio/inacessível
- Log de mudança de versão de regras
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.config import (
    AppConfig,
    QueueConfig,
    WorkerConfig,
)
from brand_watchdog.coordinator.coordinator import (
    MonitoringCoordinator,
)
from brand_watchdog.coordinator.cycle_consolidator import (
    CycleConsolidator,
)
from brand_watchdog.models.dataclasses import TargetSite
from brand_watchdog.queue.publisher import SQSPublisher
from brand_watchdog.utils.rule_set_version import (
    RuleSetDirectoryError,
    RuleSetVersionCalculator,
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


def _make_target_site(
    url: str = "https://example.com",
    site_id: str = "site-1",
    active: bool = True,
    brand: str = "sky_plus",
) -> TargetSite:
    return TargetSite(
        id=site_id,
        url=url,
        normalized_url=url.lower(),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        active=active,
        brand=brand,
    )


def _make_config() -> AppConfig:
    return AppConfig(
        queue=QueueConfig(
            queue_url="https://sqs.us-east-1.amazonaws.com/test",
            publish_timeout_minutes=5,
        ),
        worker=WorkerConfig(
            consolidation_poll_interval_seconds=30,
            consolidation_timeout_minutes=60,
        ),
    )


def _make_coordinator(
    rule_set_calculator: MagicMock | None = None,
    sqs_publisher: AsyncMock | None = None,
    consolidator: AsyncMock | None = None,
    target_site_manager: AsyncMock | None = None,
    config: AppConfig | None = None,
) -> MonitoringCoordinator:
    """Cria MonitoringCoordinator com mocks para testes."""
    if rule_set_calculator is None:
        rule_set_calculator = MagicMock(
            spec=RuleSetVersionCalculator
        )
        rule_set_calculator.calculate.return_value = (
            "v1719849600_a3b2c1d4"
        )
        rule_set_calculator.has_changed.return_value = False

    if sqs_publisher is None:
        sqs_publisher = AsyncMock(spec=SQSPublisher)
        sqs_publisher.publish_all = AsyncMock(
            return_value=(1, 0)
        )

    if consolidator is None:
        consolidator = AsyncMock(spec=CycleConsolidator)
        consolidator.consolidate = AsyncMock(
            return_value="completed"
        )

    if target_site_manager is None:
        target_site_manager = AsyncMock()
        target_site_manager.list_all = AsyncMock(
            return_value=[_make_target_site()]
        )

    _config = config or _make_config()

    return MonitoringCoordinator(
        rule_set_calculator=rule_set_calculator,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
        target_site_manager=target_site_manager,
        config=_config,
    )


# --- Testes de Ciclo Completo com Despacho ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_run_cycle_dispatches_to_sqs(
    mock_get_session_patch,
):
    """Ciclo completo despacha mensagens na fila SQS."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [
        _make_target_site(
            url="https://a.com", site_id="s1"
        ),
        _make_target_site(
            url="https://b.com", site_id="s2", brand="dgo"
        ),
    ]

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(2, 0)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        target_site_manager=target_site_manager,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
    )

    result = await coordinator.run_cycle()

    assert result.sites_processed == 2
    assert result.sites_failed == 0
    sqs_publisher.publish_all.assert_called_once()

    # Verificar mensagens publicadas
    call_args = sqs_publisher.publish_all.call_args
    messages = call_args[0][0]
    assert len(messages) == 2
    assert messages[0].site_id == "s1"
    assert messages[0].brand == "sky_plus"
    assert messages[1].site_id == "s2"
    assert messages[1].brand == "dgo"


# --- Testes de Lock de Ciclo ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_run_cycle_skipped_when_already_running(
    mock_get_session_patch,
):
    """Ciclo é pulado se o anterior ainda está em execução."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    coordinator = _make_coordinator()
    coordinator._cycle_running = True

    result = await coordinator.run_cycle()

    assert result.sites_processed == 0
    assert result.sites_failed == 0
    assert result.detections_found == 0
    assert result.site_results == []


# --- Testes de RuleSetVersionCalculator ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_aborts_on_rule_set_error(
    mock_get_session_patch,
):
    """Ciclo é abortado quando diretório de regras está
    vazio ou inacessível."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    rule_set_calculator = MagicMock(
        spec=RuleSetVersionCalculator
    )
    rule_set_calculator.calculate.side_effect = (
        RuleSetDirectoryError("Diretório vazio")
    )

    coordinator = _make_coordinator(
        rule_set_calculator=rule_set_calculator,
    )

    result = await coordinator.run_cycle()

    assert result.sites_processed == 0
    assert result.sites_failed == 0


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_rule_version_change_logged(
    mock_get_session_patch,
):
    """Log registra mudança de versão de regras."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    rule_set_calculator = MagicMock(
        spec=RuleSetVersionCalculator
    )
    rule_set_calculator.calculate.return_value = (
        "v1719850000_b4c3d2e1"
    )
    rule_set_calculator.has_changed.return_value = True

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(1, 0)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        rule_set_calculator=rule_set_calculator,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
        target_site_manager=target_site_manager,
    )

    # Simular versão anterior
    coordinator._previous_rule_version = (
        "v1719849600_a3b2c1d4"
    )

    with patch(
        "brand_watchdog.coordinator.coordinator.logger"
    ) as mock_logger:
        await coordinator.run_cycle()

    # Verificar que o log de mudança de versão foi chamado
    info_calls = mock_logger.info.call_args_list
    version_change_logged = any(
        "vers" in str(call) and "v1719849600" in str(call)
        and "v1719850000" in str(call)
        for call in info_calls
    )
    assert version_change_logged, (
        f"Log de mudança de versão não encontrado. "
        f"Calls: {info_calls}"
    )


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_rule_version_stored_in_cycle(
    mock_get_session_patch,
):
    """Versão de regras é armazenada no registro do ciclo."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    rule_set_calculator = MagicMock(
        spec=RuleSetVersionCalculator
    )
    rule_set_calculator.calculate.return_value = (
        "v1719849600_a3b2c1d4"
    )
    rule_set_calculator.has_changed.return_value = False

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(1, 0)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        rule_set_calculator=rule_set_calculator,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
        target_site_manager=target_site_manager,
    )

    await coordinator.run_cycle()

    # Verificar que add foi chamado com rule_set_version
    add_call = mock_session.add.call_args_list[0]
    cycle_model = add_call[0][0]
    assert cycle_model.rule_set_version == (
        "v1719849600_a3b2c1d4"
    )


# --- Testes de Publicação SQS ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_publish_failures_tracked(
    mock_get_session_patch,
):
    """Falhas de publicação são registradas no ciclo."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [
        _make_target_site(url="https://a.com", site_id="s1"),
        _make_target_site(url="https://b.com", site_id="s2"),
        _make_target_site(url="https://c.com", site_id="s3"),
    ]

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    # 2 publicados com sucesso, 1 falha
    sqs_publisher.publish_all = AsyncMock(
        return_value=(2, 1)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        target_site_manager=target_site_manager,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
    )

    result = await coordinator.run_cycle()

    assert result.sites_processed == 2
    assert result.sites_failed == 1


# --- Testes de Timeout de Publicação (5min) ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_publish_timeout_registers_partial_results(
    mock_get_session_patch,
):
    """Timeout de 5min na publicação registra resultados parciais.

    Validates: Requirements 1.6
    Quando a publicação excede o timeout, o coordinator
    registra os sites não publicados como falhas e prossegue
    com status 'dispatched'.
    """
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [
        _make_target_site(
            url=f"https://site{i}.com", site_id=f"s{i}"
        )
        for i in range(20)
    ]

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    # Simula publish_all retornando resultados parciais
    # (10 publicadas com sucesso, 10 como falha por timeout)
    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(10, 10)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        target_site_manager=target_site_manager,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
    )

    result = await coordinator.run_cycle()

    # Coordinator registra 10 sucessos e 10 falhas
    assert result.sites_processed == 10
    assert result.sites_failed == 10


# --- Testes de CycleConsolidator ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_consolidator_started_as_background_task(
    mock_get_session_patch,
):
    """Consolidador é iniciado como task assíncrona."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(1, 0)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        target_site_manager=target_site_manager,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
    )

    await coordinator.run_cycle()

    # Aguardar tasks pendentes
    await asyncio.sleep(0.01)

    consolidator.consolidate.assert_called_once()
    call_kwargs = consolidator.consolidate.call_args.kwargs
    assert call_kwargs["sites_dispatched"] == 1


# --- Testes de Ciclo Sem Sites ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_with_no_target_sites(
    mock_get_session_patch,
):
    """Ciclo sem target sites registrados termina sem erros."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=[])

    coordinator = _make_coordinator(
        target_site_manager=target_site_manager,
    )

    result = await coordinator.run_cycle()

    assert result.sites_processed == 0
    assert result.sites_failed == 0
    assert result.detections_found == 0
    assert result.site_results == []


# --- Testes de Concurrent Cycle Skip ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_concurrent_cycle_second_is_skipped(
    mock_get_session_patch,
):
    """Quando dois ciclos são disparados simultaneamente, o segundo
    é pulado pois o primeiro já está em execução."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]

    first_cycle_started = asyncio.Event()
    first_cycle_proceed = asyncio.Event()

    target_site_manager = AsyncMock()

    async def slow_list_all():
        first_cycle_started.set()
        await first_cycle_proceed.wait()
        return target_sites

    target_site_manager.list_all = slow_list_all

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(1, 0)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        target_site_manager=target_site_manager,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
    )

    task1 = asyncio.create_task(coordinator.run_cycle())
    await first_cycle_started.wait()

    result2 = await coordinator.run_cycle()

    first_cycle_proceed.set()
    result1 = await task1

    assert result2.sites_processed == 0
    assert result2.site_results == []
    assert result1.sites_processed == 1


# --- Testes de Messages Build ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_messages_contain_correct_fields(
    mock_get_session_patch,
):
    """Mensagens publicadas contêm todos os campos corretos."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [
        _make_target_site(
            url="https://sky.com",
            site_id="uuid-1",
            brand="sky_plus",
        ),
    ]

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    rule_set_calculator = MagicMock(
        spec=RuleSetVersionCalculator
    )
    rule_set_calculator.calculate.return_value = (
        "v1719849600_a3b2c1d4"
    )
    rule_set_calculator.has_changed.return_value = False

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(1, 0)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        rule_set_calculator=rule_set_calculator,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
        target_site_manager=target_site_manager,
    )

    await coordinator.run_cycle()

    call_args = sqs_publisher.publish_all.call_args
    messages = call_args[0][0]
    msg = messages[0]

    assert msg.site_id == "uuid-1"
    assert msg.brand == "sky_plus"
    assert msg.url == "https://sky.com"
    assert msg.rule_set_version == "v1719849600_a3b2c1d4"
    # cycle_id é gerado internamente (UUID)
    assert len(msg.cycle_id) > 0


# --- Testes de Status do Ciclo ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_created_with_dispatched_status(
    mock_get_session_patch,
):
    """Ciclo é criado com status 'dispatched'."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    sqs_publisher = AsyncMock(spec=SQSPublisher)
    sqs_publisher.publish_all = AsyncMock(
        return_value=(1, 0)
    )

    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(
        return_value="completed"
    )

    coordinator = _make_coordinator(
        target_site_manager=target_site_manager,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
    )

    await coordinator.run_cycle()

    # Primeiro add é a criação do ciclo
    add_call = mock_session.add.call_args_list[0]
    cycle_model = add_call[0][0]
    assert cycle_model.status == "dispatched"
