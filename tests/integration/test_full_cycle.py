"""Teste de integração end-to-end do ciclo de monitoramento compliance.

Valida o fluxo completo: captura → analyze_compliance → notify →
persistência → stats. Usa banco SQLite em memória (real) e mocka
apenas serviços externos:
- Playwright (Crawler) → retorna screenshot mock
- AWS Bedrock (ComplianceAnalyzer) → retorna ComplianceReport mock
- SES/SMTP (ComplianceEmailNotifier) → captura emails enviados

Requirements: 1.1, 8.1, 9.1
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from brand_watchdog.alerts.compliance_email_notifier import (
    ComplianceEmailNotifier,
)
from brand_watchdog.analyzer.compliance_analyzer import ComplianceAnalyzer
from brand_watchdog.config import (
    AlertConfig,
    AnalyzerConfig,
    AppConfig,
    CrawlerConfig,
    StorageConfig,
)
from brand_watchdog.coordinator.coordinator import MonitoringCoordinator
from brand_watchdog.crawler.crawler import Crawler
from brand_watchdog.models.database import (
    close_db,
    get_session,
    init_db,
    setup_database,
)
from brand_watchdog.models.dataclasses import (
    CaptureResult,
    ComplianceReport,
    ComplianceRuleResult,
)
from brand_watchdog.models.entities import (
    MonitoringCycleModel,
    ScreenshotModel,
    TargetSiteModel,
)
from brand_watchdog.registry.target_site_manager import TargetSiteManager
from brand_watchdog.storage.detection_store import DetectionStore
from brand_watchdog.storage.screenshot_store import ScreenshotStore


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
def mock_crawler(tmp_path: Path) -> AsyncMock:
    """Mock do Crawler que retorna screenshots simulados."""
    crawler = AsyncMock(spec=Crawler)
    fake_png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    async def capture_side_effect(
        target_url: str,
    ) -> CaptureResult:
        screenshot_ref_id = str(uuid.uuid4())
        screenshot_path = tmp_path / f"{screenshot_ref_id}.png"
        screenshot_path.write_bytes(fake_png_bytes)
        return CaptureResult(
            target_url=target_url,
            screenshot_path=screenshot_path,
            screenshot_ref_id=screenshot_ref_id,
            captured_at=datetime.now(timezone.utc),
            page_height_px=3000,
            was_truncated=False,
            success=True,
        )

    crawler.capture = AsyncMock(side_effect=capture_side_effect)
    return crawler


@pytest.fixture
def mock_compliance_analyzer() -> AsyncMock:
    """Mock do ComplianceAnalyzer que retorna relatório compliant."""
    analyzer = AsyncMock(spec=ComplianceAnalyzer)

    async def analyze_side_effect(
        screenshot_path: Path,
        target_url: str,
        screenshot_ref_id: str,
        cycle_id: str,
        brand: str | None = None,
    ) -> ComplianceReport:
        return ComplianceReport(
            target_url=target_url,
            analyzed_at=datetime.now(timezone.utc),
            overall_status="compliant",
            rule_results=[
                ComplianceRuleResult(
                    rule_id="facilitator_role",
                    status="PASS",
                    confidence=92,
                    description="SKY+ referenciado corretamente",
                ),
                ComplianceRuleResult(
                    rule_id="logo_application",
                    status="PASS",
                    confidence=88,
                    description="Logos em ordem correta",
                ),
                ComplianceRuleResult(
                    rule_id="logo_effects",
                    status="PASS",
                    confidence=90,
                    description="Sem efeitos indevidos",
                ),
                ComplianceRuleResult(
                    rule_id="content_separation",
                    status="PASS",
                    confidence=85,
                    description="Conteúdo separado OK",
                ),
                ComplianceRuleResult(
                    rule_id="naming_pricing",
                    status="PASS",
                    confidence=95,
                    description="Nomenclatura correta",
                ),
                ComplianceRuleResult(
                    rule_id="kv_integrity",
                    status="PASS",
                    confidence=91,
                    description="KV íntegro",
                ),
            ],
            screenshot_ref_id=screenshot_ref_id,
            cycle_id=cycle_id,
        )

    analyzer.analyze_compliance = AsyncMock(
        side_effect=analyze_side_effect
    )
    return analyzer


@pytest.fixture
def mock_compliance_notifier() -> AsyncMock:
    """Mock do ComplianceEmailNotifier que captura envios."""
    notifier = AsyncMock(spec=ComplianceEmailNotifier)
    notifier.send_compliance_report = AsyncMock(return_value=True)
    return notifier


# --- Testes de Integração ---


@pytest.mark.integration
class TestFullMonitoringCycle:
    """Testes de integração para o ciclo completo de compliance."""

    async def test_full_cycle_end_to_end(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_compliance_analyzer: AsyncMock,
        mock_compliance_notifier: AsyncMock,
    ) -> None:
        """Ciclo completo: captura → analyze_compliance → notify.

        Verifica:
        - Todos os sites foram capturados
        - Análise de compliance executada para cada site
        - Relatório enviado para cada site
        - CycleResult stats estão corretos
        - MonitoringCycleModel criado e atualizado no banco
        """
        sites = seed_data["sites"]
        config = seed_data["config"]

        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )

        coordinator = MonitoringCoordinator(
            crawler=mock_crawler,
            compliance_analyzer=mock_compliance_analyzer,
            compliance_notifier=mock_compliance_notifier,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        # Verificação 1: Captura
        assert mock_crawler.capture.call_count == 2
        captured_urls = sorted(
            call.args[0]
            for call in mock_crawler.capture.call_args_list
        )
        expected_urls = sorted(s.url for s in sites)
        assert captured_urls == expected_urls

        # Verificação 2: Análise de compliance
        assert (
            mock_compliance_analyzer.analyze_compliance.call_count
            == 2
        )

        # Verificação 3: Notificação enviada para cada site
        assert (
            mock_compliance_notifier.send_compliance_report
            .call_count == 2
        )

        # Verificação 4: CycleResult stats
        assert cycle_result.sites_processed == 2
        assert cycle_result.sites_failed == 0
        assert len(cycle_result.site_results) == 2
        for sr in cycle_result.site_results:
            assert sr.success is True

        # Verificação 5: MonitoringCycleModel no banco
        async with get_session() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_result.cycle_id
            )
            result = await session.execute(stmt)
            cycle_model = result.scalar_one_or_none()

        assert cycle_model is not None
        assert cycle_model.status == "completed"
        assert cycle_model.sites_processed == 2
        assert cycle_model.sites_failed == 0
        assert cycle_model.started_at is not None
        assert cycle_model.ended_at is not None
        assert cycle_model.ended_at >= cycle_model.started_at

    async def test_cycle_with_failed_site(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_compliance_analyzer: AsyncMock,
        mock_compliance_notifier: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Ciclo com um site falhando: verifica isolamento de falha."""
        config = seed_data["config"]

        crawler = AsyncMock(spec=Crawler)
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        call_counter = {"count": 0}

        async def capture_partial_fail(
            target_url: str,
        ) -> CaptureResult:
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                return CaptureResult(
                    target_url=target_url,
                    screenshot_path=Path(""),
                    screenshot_ref_id=str(uuid.uuid4()),
                    captured_at=datetime.now(timezone.utc),
                    page_height_px=0,
                    was_truncated=False,
                    success=False,
                    error_message="Timeout de 60s",
                )
            else:
                ref_id = str(uuid.uuid4())
                path = tmp_path / f"{ref_id}.png"
                path.write_bytes(fake_png)
                return CaptureResult(
                    target_url=target_url,
                    screenshot_path=path,
                    screenshot_ref_id=ref_id,
                    captured_at=datetime.now(timezone.utc),
                    page_height_px=2000,
                    was_truncated=False,
                    success=True,
                )

        crawler.capture = AsyncMock(
            side_effect=capture_partial_fail
        )

        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )

        coordinator = MonitoringCoordinator(
            crawler=crawler,
            compliance_analyzer=mock_compliance_analyzer,
            compliance_notifier=mock_compliance_notifier,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        assert cycle_result.sites_processed == 1
        assert cycle_result.sites_failed == 1
        # Análise chamada apenas para o site com sucesso
        assert (
            mock_compliance_analyzer.analyze_compliance.call_count
            == 1
        )
        # Notificação apenas para o site com sucesso
        assert (
            mock_compliance_notifier.send_compliance_report
            .call_count == 1
        )

        # MonitoringCycleModel reflete a falha parcial
        async with get_session() as session:
            stmt = select(MonitoringCycleModel).where(
                MonitoringCycleModel.id == cycle_result.cycle_id
            )
            result = await session.execute(stmt)
            cycle_model = result.scalar_one()

        assert cycle_model.status == "completed"
        assert cycle_model.sites_processed == 1
        assert cycle_model.sites_failed == 1

    async def test_email_always_sent_regardless_of_status(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_compliance_notifier: AsyncMock,
    ) -> None:
        """Email é enviado independentemente do status (compliant
        ou non_compliant)."""
        config = seed_data["config"]

        # Analyzer que retorna non_compliant
        analyzer_fail = AsyncMock(spec=ComplianceAnalyzer)

        async def analyze_non_compliant(
            screenshot_path: Path,
            target_url: str,
            screenshot_ref_id: str,
            cycle_id: str,
            brand: str | None = None,
        ) -> ComplianceReport:
            return ComplianceReport(
                target_url=target_url,
                analyzed_at=datetime.now(timezone.utc),
                overall_status="non_compliant",
                rule_results=[
                    ComplianceRuleResult(
                        rule_id="facilitator_role",
                        status="FAIL",
                        confidence=87,
                        description="Amazon sem referência SKY+",
                    ),
                ],
                screenshot_ref_id=screenshot_ref_id,
                cycle_id=cycle_id,
            )

        analyzer_fail.analyze_compliance = AsyncMock(
            side_effect=analyze_non_compliant
        )

        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )

        coordinator = MonitoringCoordinator(
            crawler=mock_crawler,
            compliance_analyzer=analyzer_fail,
            compliance_notifier=mock_compliance_notifier,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        # Email enviado para todos os 2 sites (mesmo non_compliant)
        assert (
            mock_compliance_notifier.send_compliance_report
            .call_count == 2
        )
        assert cycle_result.sites_processed == 2

    async def test_cycle_screenshots_persisted_in_db(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_compliance_analyzer: AsyncMock,
        mock_compliance_notifier: AsyncMock,
    ) -> None:
        """Verifica que screenshots são persistidos no banco."""
        config = seed_data["config"]

        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )

        coordinator = MonitoringCoordinator(
            crawler=mock_crawler,
            compliance_analyzer=mock_compliance_analyzer,
            compliance_notifier=mock_compliance_notifier,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        async with get_session() as session:
            stmt = select(ScreenshotModel)
            result = await session.execute(stmt)
            screenshots = result.scalars().all()

        assert len(screenshots) == 2
        for ss in screenshots:
            assert ss.monitoring_cycle_id == cycle_result.cycle_id
            assert ss.height_px == 3000
            assert ss.was_truncated is False
            assert ss.captured_at is not None
            assert ss.expires_at is not None
