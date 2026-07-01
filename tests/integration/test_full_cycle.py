"""Teste de integração end-to-end do ciclo de monitoramento.

Valida o fluxo completo: captura → análise → alerta → persistência → stats.
Usa banco SQLite em memória (real) e mocka apenas serviços externos:
- Playwright (Crawler) → retorna screenshot mock
- AWS Bedrock (Analyzer) → retorna detecção mock em JSON
- SES/SMTP (AlertService) → captura emails enviados

Requirements: 5.1, 5.3, 5.6
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from brand_watchdog.alerts.alert_service import AlertService, EmailProvider
from brand_watchdog.analyzer.analyzer import Analyzer
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
    BoundingBox,
    CaptureResult,
    DetectionResult,
)
from brand_watchdog.models.entities import (
    BrandAssetModel,
    DetectionResultModel,
    MonitoringCycleModel,
    ScreenshotModel,
    TargetSiteModel,
)
from brand_watchdog.registry.brand_registry import BrandRegistry
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
    """Cria Target Sites e Brand Assets reais no banco de dados."""
    sites = []
    assets = []

    async with get_session() as session:
        # Cria 2 Target Sites
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

        # Cria Brand Assets (1 logo + 1 texto)
        asset_logo = BrandAssetModel(
            id=str(uuid.uuid4()),
            asset_type="logo",
            file_path="/fake/logo.png",
            text_value=None,
            content_hash="abc123hash",
            original_filename="brand_logo.png",
            file_size_bytes=2048,
        )
        asset_text = BrandAssetModel(
            id=str(uuid.uuid4()),
            asset_type="text",
            file_path=None,
            text_value="MinhaMarca",
            content_hash="def456hash",
            original_filename=None,
            file_size_bytes=None,
        )
        session.add(asset_logo)
        session.add(asset_text)
        assets = [asset_logo, asset_text]

    return {"sites": sites, "assets": assets, "config": setup_db}


@pytest.fixture
def app_config(setup_db: StorageConfig, tmp_path: Path) -> AppConfig:
    """Cria AppConfig com threshold para alertas."""
    return AppConfig(
        crawler=CrawlerConfig(),
        analyzer=AnalyzerConfig(confidence_threshold=70),
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
def mock_email_provider() -> AsyncMock:
    """Mock do provedor de email que captura envios."""
    provider = AsyncMock(spec=EmailProvider)
    provider.send = AsyncMock(return_value=None)
    return provider


@pytest.fixture
def mock_crawler(tmp_path: Path) -> AsyncMock:
    """Mock do Crawler que retorna screenshots simulados."""
    crawler = AsyncMock(spec=Crawler)

    # Cria um arquivo PNG fake no filesystem para cada captura
    fake_png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    async def capture_side_effect(target_url: str) -> CaptureResult:
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
def mock_analyzer() -> AsyncMock:
    """Mock do Analyzer que retorna detecções simuladas."""
    analyzer = AsyncMock(spec=Analyzer)

    async def analyze_side_effect(
        screenshot_path: Path,
        brand_assets: list,
        target_url: str = "",
        screenshot_ref_id: str = "",
    ) -> list[DetectionResult]:
        """Retorna 1 detecção por site com confidence alta."""
        now = datetime.now(timezone.utc)
        return [
            DetectionResult(
                target_url=target_url,
                match_type="logo",
                confidence=85,
                bounding_box=BoundingBox(
                    x_percent=10.0,
                    y_percent=20.0,
                    width_percent=15.0,
                    height_percent=8.0,
                ),
                description="Logo da marca encontrado no header",
                detected_at=now,
                screenshot_ref_id=screenshot_ref_id,
            ),
        ]

    analyzer.analyze = AsyncMock(side_effect=analyze_side_effect)
    return analyzer


# --- Testes de Integração ---


@pytest.mark.integration
class TestFullMonitoringCycle:
    """Testes de integração para o ciclo completo de monitoramento."""

    async def test_full_cycle_end_to_end(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_analyzer: AsyncMock,
        mock_email_provider: AsyncMock,
    ) -> None:
        """Ciclo completo: captura → análise → alerta → persistência.

        Verifica:
        - Todos os sites foram capturados
        - Análise foi executada com brand assets corretos
        - DetectionResults foram persistidos no banco
        - Alertas foram enviados para detecções acima do threshold
        - CycleResult stats estão corretos
        - MonitoringCycleModel foi criado e atualizado no banco
        """
        sites = seed_data["sites"]
        config = seed_data["config"]

        # Instancia componentes reais com stores reais (banco real)
        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        brand_registry = BrandRegistry(
            logo_storage_path=config.screenshot_base_path / "logos"
        )
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )

        # AlertService com email provider mockado
        alert_service = AlertService(
            config=app_config.alert,
            detection_store=detection_store,
            email_provider=mock_email_provider,
        )

        # Monta o coordinator com mocks externos + stores reais
        coordinator = MonitoringCoordinator(
            crawler=mock_crawler,
            analyzer=mock_analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        # Executa ciclo completo
        cycle_result = await coordinator.run_cycle()

        # --- Verificação 1: Captura ---
        # Crawler deve ter sido chamado para cada site
        assert mock_crawler.capture.call_count == 2
        captured_urls = sorted(
            call.args[0]
            for call in mock_crawler.capture.call_args_list
        )
        expected_urls = sorted(s.url for s in sites)
        assert captured_urls == expected_urls

        # --- Verificação 2: Análise ---
        # Analyzer deve ter sido chamado para cada site
        assert mock_analyzer.analyze.call_count == 2
        for call in mock_analyzer.analyze.call_args_list:
            kwargs = call.kwargs
            # Verifica que brand_assets foram passados
            assert len(kwargs["brand_assets"]) == 2

        # --- Verificação 3: Persistência de DetectionResults ---
        async with get_session() as session:
            stmt = select(DetectionResultModel)
            result = await session.execute(stmt)
            detection_models = result.scalars().all()

        # 1 detecção por site = 2 detecções persistidas
        assert len(detection_models) == 2
        for dm in detection_models:
            assert dm.match_type == "logo"
            assert dm.confidence == 85
            assert dm.description == "Logo da marca encontrado no header"

        # --- Verificação 4: Alertas ---
        # Confiança 85 >= threshold 70 → alertas devem ser enviados
        # 2 detecções × 2 destinatários = 4 emails
        assert mock_email_provider.send.call_count == 4

        # Verifica que os destinatários corretos foram usados
        recipients_called = [
            call.kwargs["recipient"]
            for call in mock_email_provider.send.call_args_list
        ]
        assert recipients_called.count("owner@empresa.com") == 2
        assert recipients_called.count("legal@empresa.com") == 2

        # --- Verificação 5: CycleResult stats ---
        assert cycle_result.sites_processed == 2
        assert cycle_result.sites_failed == 0
        assert cycle_result.detections_found == 2
        assert len(cycle_result.site_results) == 2
        for sr in cycle_result.site_results:
            assert sr.success is True
            assert len(sr.detections) == 1

        # --- Verificação 6: MonitoringCycleModel no banco ---
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
        assert cycle_model.detections_found == 2
        assert cycle_model.started_at is not None
        assert cycle_model.ended_at is not None
        assert cycle_model.ended_at >= cycle_model.started_at

    async def test_cycle_with_failed_site(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_analyzer: AsyncMock,
        mock_email_provider: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Ciclo com um site falhando: verifica isolamento de falha.

        Req 5.5: Se um site falhar, o ciclo continua com os demais.
        """
        sites = seed_data["sites"]
        config = seed_data["config"]

        # Crawler que falha no primeiro site e sucede no segundo
        crawler = AsyncMock(spec=Crawler)
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        call_counter = {"count": 0}

        async def capture_partial_fail(target_url: str) -> CaptureResult:
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                # Primeiro site falha
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
                # Segundo site OK
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

        crawler.capture = AsyncMock(side_effect=capture_partial_fail)

        # Componentes reais
        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        brand_registry = BrandRegistry(
            logo_storage_path=config.screenshot_base_path / "logos"
        )
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )
        alert_service = AlertService(
            config=app_config.alert,
            detection_store=detection_store,
            email_provider=mock_email_provider,
        )

        coordinator = MonitoringCoordinator(
            crawler=crawler,
            analyzer=mock_analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        # 1 processado com sucesso, 1 falhou
        assert cycle_result.sites_processed == 1
        assert cycle_result.sites_failed == 1
        # Apenas 1 detecção (do site que não falhou)
        assert cycle_result.detections_found == 1

        # Analyzer chamado apenas para o site que obteve sucesso
        assert mock_analyzer.analyze.call_count == 1

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

    async def test_cycle_no_alerts_below_threshold(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_email_provider: AsyncMock,
    ) -> None:
        """Detecções abaixo do threshold não geram alertas."""
        config = seed_data["config"]

        # Analyzer retorna detecção com confidence baixa (50 < 70)
        analyzer_low = AsyncMock(spec=Analyzer)

        async def analyze_low_confidence(
            screenshot_path: Path,
            brand_assets: list,
            target_url: str = "",
            screenshot_ref_id: str = "",
        ) -> list[DetectionResult]:
            now = datetime.now(timezone.utc)
            return [
                DetectionResult(
                    target_url=target_url,
                    match_type="text",
                    confidence=50,
                    bounding_box=BoundingBox(
                        x_percent=5.0,
                        y_percent=80.0,
                        width_percent=20.0,
                        height_percent=3.0,
                    ),
                    description="Texto similar encontrado no footer",
                    detected_at=now,
                    screenshot_ref_id=screenshot_ref_id,
                ),
            ]

        analyzer_low.analyze = AsyncMock(
            side_effect=analyze_low_confidence
        )

        # Componentes reais
        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        brand_registry = BrandRegistry(
            logo_storage_path=config.screenshot_base_path / "logos"
        )
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )
        alert_service = AlertService(
            config=app_config.alert,
            detection_store=detection_store,
            email_provider=mock_email_provider,
        )

        coordinator = MonitoringCoordinator(
            crawler=mock_crawler,
            analyzer=analyzer_low,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        # Detecções foram encontradas e persistidas
        assert cycle_result.detections_found == 2
        assert cycle_result.sites_processed == 2

        # Mas nenhum alerta enviado (confidence 50 < threshold 70)
        assert mock_email_provider.send.call_count == 0

    async def test_cycle_screenshots_persisted_in_db(
        self,
        seed_data: dict,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_analyzer: AsyncMock,
        mock_email_provider: AsyncMock,
    ) -> None:
        """Verifica que screenshots são persistidos no banco com metadados."""
        config = seed_data["config"]

        detection_store = DetectionStore(config)
        screenshot_store = ScreenshotStore(config)
        brand_registry = BrandRegistry(
            logo_storage_path=config.screenshot_base_path / "logos"
        )
        target_site_manager = TargetSiteManager(
            max_target_sites=app_config.max_target_sites
        )
        alert_service = AlertService(
            config=app_config.alert,
            detection_store=detection_store,
            email_provider=mock_email_provider,
        )

        coordinator = MonitoringCoordinator(
            crawler=mock_crawler,
            analyzer=mock_analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
            config=app_config,
        )

        cycle_result = await coordinator.run_cycle()

        # Verifica screenshots persistidos
        async with get_session() as session:
            stmt = select(ScreenshotModel)
            result = await session.execute(stmt)
            screenshots = result.scalars().all()

        # 2 sites = 2 screenshots
        assert len(screenshots) == 2
        for ss in screenshots:
            assert ss.monitoring_cycle_id == cycle_result.cycle_id
            assert ss.height_px == 3000
            assert ss.was_truncated is False
            assert ss.captured_at is not None
            assert ss.expires_at is not None
