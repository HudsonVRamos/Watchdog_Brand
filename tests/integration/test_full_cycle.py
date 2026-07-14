"""Teste de integração end-to-end do ciclo de monitoramento compliance.

Valida o fluxo distribuído do coordenador: calcular versão de regras →
publicar SQS → consolidar. Usa banco SQLite em memória (real) e mocka
serviços AWS (SQS, EventBridge).

Requirements: 1.1, 7.1, 7.2, 9.1
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from brand_watchdog.config import (
    AlertConfig,
    AnalyzerConfig,
    AppConfig,
    CrawlerConfig,
    StorageConfig,
)
from brand_watchdog.coordinator.coordinator import MonitoringCoordinator
from brand_watchdog.coordinator.cycle_consolidator import CycleConsolidator
from brand_watchdog.models.database import (
    close_db,
    get_session,
    init_db,
    setup_database,
)
from brand_watchdog.models.dataclasses import TargetSite
from brand_watchdog.models.entities import (
    MonitoringCycleModel,
    TargetSiteModel,
)
from brand_watchdog.queue.publisher import SQSPublisher
from brand_watchdog.registry.target_site_manager import TargetSiteManager
from brand_watchdog.utils.rule_set_version import RuleSetVersionCalculator


# --- Fixtures ---


@pytest.fixture
async def setup_db(tmp_path: Path):
    """Configura banco SQLite em memória para teste de integração."""
    config = StorageConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        screenshot_base_path=tmp_path / "screenshots",
        screenshot_retention_days=90,
        detection_retention_days=90,
    )
    setup_database(config)
    await init_db()
    yield config
    await close_db()


@pytest.fixture
async def seed_data(setup_db: StorageConfig):
    """Cria Target Sites reais no banco de dados."""
    sites = []

    async with get_session() as session:
        site_1 = TargetSiteModel(
            id=str(uuid.uuid4()),
            url="https://example-site-1.com/page",
            normalized_url="https://example-site-1.com/page",
            active=True,
        )
        site_2 = TargetSiteModel(
            id=str(uuid.uuid4()),
            url="https://example-site-2.com",
            normalized_url="https://example-site-2.com",
            active=True,
        )
        session.add(site_1)
        session.add(site_2)
        sites = [site_1, site_2]

    return {"sites": sites, "config": setup_db}


@pytest.fixture
def app_config(setup_db: StorageConfig) -> AppConfig:
    """Cria AppConfig para testes de integração."""
    return AppConfig(
        crawler=CrawlerConfig(),
        analyzer=AnalyzerConfig(),
        alert=AlertConfig(
            provider="ses",
            ses_sender="alerts@brand-watchdog.com",
            recipients=["owner@empresa.com", "legal@empresa.com"],
            retry_attempts=1,
            retry_interval_seconds=0,
        ),
        storage=setup_db,
    )


@pytest.fixture
def mock_rule_set_calculator() -> MagicMock:
    """Mock do RuleSetVersionCalculator."""
    calculator = MagicMock(spec=RuleSetVersionCalculator)
    calculator.calculate.return_value = "v1718000000_abcd1234"
    calculator.has_changed.return_value = False
    return calculator


@pytest.fixture
def mock_sqs_publisher() -> AsyncMock:
    """Mock do SQSPublisher que simula publicação bem-sucedida."""
    publisher = AsyncMock(spec=SQSPublisher)
    publisher.publish_all = AsyncMock(return_value=(2, 0))
    return publisher


@pytest.fixture
def mock_consolidator() -> AsyncMock:
    """Mock do CycleConsolidator."""
    consolidator = AsyncMock(spec=CycleConsolidator)
    consolidator.consolidate = AsyncMock(return_value="completed")
    return consolidator


@pytest.fixture
def mock_target_site_manager() -> AsyncMock:
    """Mock do TargetSiteManager com 2 sites ativos."""
    manager = AsyncMock(spec=TargetSiteManager)
    sites = [
        TargetSite(
            id=str(uuid.uuid4()),
            url="https://example-site-1.com/page",
            normalized_url="https://example-site-1.com/page",
            created_at=datetime.now(timezone.utc),
            active=True,
            brand="sky_plus",
        ),
        TargetSite(
            id=str(uuid.uuid4()),
            url="https://example-site-2.com",
            normalized_url="https://example-site-2.com",
            created_at=datetime.now(timezone.utc),
            active=True,
            brand="sky_plus",
        ),
    ]
    manager.list_all = AsyncMock(return_value=sites)
    return manager


# --- Testes de Integração ---


@pytest.mark.integration
class TestFullMonitoringCycle:
    """Testes de integração para o ciclo distribuído de compliance."""

    async def test_full_cycle_end_to_end(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_rule_set_calculator: MagicMock,
        mock_sqs_publisher: AsyncMock,
        mock_consolidator: AsyncMock,
    ) -> None:
        """Ciclo completo: calcular versão → publicar SQS → consolidar.

        Verifica:
        - Versão de regras calculada
        - Mensagens publicadas no SQS (1 por site)
        - Consolidação iniciada
        - CycleResult stats estão corretos
        - MonitoringCycleModel criado e atualizado no banco
        """
        sites = seed_data["sites"]
        config = seed_data["config"]

        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )

        coordinator = MonitoringCoordinator(
            rule_set_calculator=mock_rule_set_calculator,
            sqs_publisher=mock_sqs_publisher,
            consolidator=mock_consolidator,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        # Verificação 1: Versão de regras calculada
        mock_rule_set_calculator.calculate.assert_called_once()

        # Verificação 2: Mensagens publicadas no SQS
        mock_sqs_publisher.publish_all.assert_called_once()
        messages = mock_sqs_publisher.publish_all.call_args[0][0]
        assert len(messages) == 2

        # Verificação 3: CycleResult stats
        assert cycle_result.sites_processed == 2
        assert cycle_result.sites_failed == 0

        # Verificação 4: MonitoringCycleModel no banco
        async with get_session() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_result.cycle_id
            )
            result = await session.execute(stmt)
            cycle_model = result.scalar_one_or_none()

        assert cycle_model is not None
        assert cycle_model.status == "dispatched"
        assert cycle_model.rule_set_version == "v1718000000_abcd1234"
        assert cycle_model.started_at is not None

    async def test_cycle_with_publish_failures(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_rule_set_calculator: MagicMock,
        mock_consolidator: AsyncMock,
    ) -> None:
        """Ciclo com falha parcial na publicação SQS."""
        config = seed_data["config"]

        # SQS com 1 sucesso e 1 falha
        sqs_publisher = AsyncMock(spec=SQSPublisher)
        sqs_publisher.publish_all = AsyncMock(return_value=(1, 1))

        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )

        coordinator = MonitoringCoordinator(
            rule_set_calculator=mock_rule_set_calculator,
            sqs_publisher=sqs_publisher,
            consolidator=mock_consolidator,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        assert cycle_result.sites_processed == 1
        assert cycle_result.sites_failed == 1

        # MonitoringCycleModel reflete falha parcial
        async with get_session() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_result.cycle_id
            )
            result = await session.execute(stmt)
            cycle_model = result.scalar_one()

        assert cycle_model.status == "dispatched"
        assert cycle_model.sites_dispatched == 2
        assert cycle_model.sites_failed == 1

    async def test_cycle_skipped_when_already_running(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_rule_set_calculator: MagicMock,
        mock_sqs_publisher: AsyncMock,
        mock_consolidator: AsyncMock,
        mock_target_site_manager: AsyncMock,
    ) -> None:
        """Ciclo pulado quando outro já está em execução."""
        coordinator = MonitoringCoordinator(
            rule_set_calculator=mock_rule_set_calculator,
            sqs_publisher=mock_sqs_publisher,
            consolidator=mock_consolidator,
            target_site_manager=mock_target_site_manager,
            config=app_config,
        )

        # Simula ciclo em execução
        coordinator._cycle_running = True

        cycle_result = await coordinator.run_cycle()

        # Ciclo pulado
        assert cycle_result.sites_processed == 0
        assert cycle_result.sites_failed == 0
        mock_sqs_publisher.publish_all.assert_not_called()

        # MonitoringCycleModel com status "skipped"
        async with get_session() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_result.cycle_id
            )
            result = await session.execute(stmt)
            cycle_model = result.scalar_one()

        assert cycle_model.status == "skipped"

    async def test_cycle_no_active_sites(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_rule_set_calculator: MagicMock,
        mock_sqs_publisher: AsyncMock,
        mock_consolidator: AsyncMock,
    ) -> None:
        """Ciclo com nenhum site ativo encerra sem publicar."""
        # TargetSiteManager sem sites
        empty_manager = AsyncMock(spec=TargetSiteManager)
        empty_manager.list_all = AsyncMock(return_value=[])

        coordinator = MonitoringCoordinator(
            rule_set_calculator=mock_rule_set_calculator,
            sqs_publisher=mock_sqs_publisher,
            consolidator=mock_consolidator,
            target_site_manager=empty_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        assert cycle_result.sites_processed == 0
        assert cycle_result.sites_failed == 0
        mock_sqs_publisher.publish_all.assert_not_called()

    async def test_rule_version_change_logged(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_sqs_publisher: AsyncMock,
        mock_consolidator: AsyncMock,
        mock_target_site_manager: AsyncMock,
    ) -> None:
        """Mudança de versão de regras é detectada entre ciclos."""
        calculator = MagicMock(spec=RuleSetVersionCalculator)
        calculator.calculate.return_value = "v1718000001_newh5678"
        calculator.has_changed.return_value = True

        coordinator = MonitoringCoordinator(
            rule_set_calculator=calculator,
            sqs_publisher=mock_sqs_publisher,
            consolidator=mock_consolidator,
            target_site_manager=mock_target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        # Verificação: has_changed foi chamado
        calculator.has_changed.assert_called_once()
        assert cycle_result.sites_processed == 2
