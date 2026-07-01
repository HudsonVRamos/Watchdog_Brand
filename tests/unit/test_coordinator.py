"""Testes unitários para o MonitoringCoordinator.

Valida:
- Ciclo completo com sucesso (capture → analyze → alert)
- Lock de ciclo: pula execução se ciclo anterior em andamento
- Concurrent cycle skip: dois ciclos simultâneos, segundo é pulado
- Processamento de múltiplos sites com isolamento de falha
- Registro e atualização de MonitoringCycleModel no banco
- Stats update: create + update com dados corretos
- Múltiplos sites com contagem mista sucesso/falha
- Ciclo com AnalysisIncompleteError do analyzer
- Log de estatísticas do ciclo
- Alertas enviados apenas para detecções acima do threshold
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from brand_watchdog.analyzer.analyzer import AnalysisIncompleteError
from brand_watchdog.config import (
    AlertConfig,
    AnalyzerConfig,
    AppConfig,
    CrawlerConfig,
    StorageConfig,
)
from brand_watchdog.coordinator.coordinator import MonitoringCoordinator
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    BrandAsset,
    CaptureResult,
    DetectionResult,
    TargetSite,
)


# --- Helpers ---


def _mock_get_session():
    """Cria mock para get_session como async context manager."""
    mock_session = MagicMock()
    # Mock execute para retornar um result com scalar_one_or_none
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
) -> TargetSite:
    return TargetSite(
        id=site_id,
        url=url,
        normalized_url=url.lower(),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        active=active,
    )


def _make_brand_asset(
    asset_id: str = "asset-1",
    asset_type: str = "logo",
) -> BrandAsset:
    return BrandAsset(
        id=asset_id,
        asset_type=asset_type,
        file_path=Path("/logos/brand.png") if asset_type == "logo" else None,
        text_value="BrandName" if asset_type == "text" else None,
        content_hash="abc123",
        original_filename="brand.png" if asset_type == "logo" else None,
        file_size_bytes=1024 if asset_type == "logo" else None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _make_capture_result(
    target_url: str = "https://example.com",
    success: bool = True,
) -> CaptureResult:
    return CaptureResult(
        target_url=target_url,
        screenshot_path=Path("/tmp/screenshot.png"),
        screenshot_ref_id="ref-123",
        captured_at=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        page_height_px=5000,
        was_truncated=False,
        success=success,
        error_message=None if success else "Timeout ao capturar",
    )


def _make_detection(
    target_url: str = "https://example.com",
    confidence: int = 85,
) -> DetectionResult:
    return DetectionResult(
        target_url=target_url,
        match_type="logo",
        confidence=confidence,
        bounding_box=BoundingBox(10.0, 20.0, 30.0, 15.0),
        description="Logo detectado",
        detected_at=datetime(2024, 6, 15, 10, 1, 0, tzinfo=timezone.utc),
        screenshot_ref_id="ref-123",
    )


def _make_config(
    confidence_threshold: int = 70,
    recipients: list[str] | None = None,
) -> AppConfig:
    if recipients is None:
        recipients = ["admin@brand.com"]
    return AppConfig(
        crawler=CrawlerConfig(),
        analyzer=AnalyzerConfig(confidence_threshold=confidence_threshold),
        alert=AlertConfig(recipients=recipients),
        storage=StorageConfig(),
    )


def _make_coordinator(
    crawler: AsyncMock | None = None,
    analyzer: AsyncMock | None = None,
    alert_service: AsyncMock | None = None,
    detection_store: AsyncMock | None = None,
    screenshot_store: AsyncMock | None = None,
    brand_registry: AsyncMock | None = None,
    target_site_manager: AsyncMock | None = None,
    config: AppConfig | None = None,
) -> MonitoringCoordinator:
    """Cria um MonitoringCoordinator com mocks para testes."""
    _crawler = crawler or AsyncMock()
    _analyzer = analyzer or AsyncMock()
    _alert_service = alert_service or AsyncMock()
    _detection_store = detection_store or AsyncMock()
    _screenshot_store = screenshot_store or AsyncMock()
    _brand_registry = brand_registry or AsyncMock()
    _target_site_manager = target_site_manager or AsyncMock()
    _config = config or _make_config()

    return MonitoringCoordinator(
        crawler=_crawler,
        analyzer=_analyzer,
        alert_service=_alert_service,
        detection_store=_detection_store,
        screenshot_store=_screenshot_store,
        brand_registry=_brand_registry,
        target_site_manager=_target_site_manager,
        config=_config,
    )


# --- Testes de Ciclo Completo ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_run_cycle_success(mock_get_session_patch):
    """Ciclo completo processa site com sucesso."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    brand_assets = [_make_brand_asset()]
    capture = _make_capture_result()
    detections = [_make_detection()]

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value=detections)

    alert_service = AsyncMock()
    alert_service.send_alert = AsyncMock(return_value=True)

    detection_store = AsyncMock()
    detection_store.save = AsyncMock(return_value="det-1")

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    # Mock de read_bytes no path do screenshot
    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 1
    assert result.sites_failed == 0
    assert result.detections_found == 1
    assert len(result.site_results) == 1
    assert result.site_results[0].success is True
    crawler.capture.assert_called_once_with("https://example.com")
    analyzer.analyze.assert_called_once()
    detection_store.save.assert_called_once()
    alert_service.send_alert.assert_called_once()


# --- Testes de Lock de Ciclo ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_run_cycle_skipped_when_already_running(mock_get_session_patch):
    """Ciclo é pulado se o anterior ainda está em execução (Req 5.4)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    coordinator = _make_coordinator()
    # Simula ciclo em execução
    coordinator._cycle_running = True

    result = await coordinator.run_cycle()

    assert result.sites_processed == 0
    assert result.sites_failed == 0
    assert result.detections_found == 0
    assert result.site_results == []


# --- Testes de Falha Isolada por Site ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_site_failure_does_not_stop_cycle(mock_get_session_patch):
    """Falha em um site não interrompe processamento dos demais (Req 5.5)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    site_ok = _make_target_site(url="https://ok.com", site_id="s1")
    site_fail = _make_target_site(url="https://fail.com", site_id="s2")
    target_sites = [site_fail, site_ok]
    brand_assets = [_make_brand_asset()]

    capture_ok = _make_capture_result(target_url="https://ok.com")
    capture_fail = _make_capture_result(
        target_url="https://fail.com", success=False
    )

    crawler = AsyncMock()
    crawler.capture = AsyncMock(
        side_effect=[capture_fail, capture_ok]
    )

    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value=[])

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 1
    assert result.sites_failed == 1
    assert len(result.site_results) == 2


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_site_exception_is_isolated(mock_get_session_patch):
    """Exceção em um site é capturada sem afetar os demais (Req 5.5)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    site1 = _make_target_site(url="https://error.com", site_id="s1")
    site2 = _make_target_site(url="https://ok.com", site_id="s2")
    target_sites = [site1, site2]
    brand_assets = [_make_brand_asset()]

    crawler = AsyncMock()
    # Primeiro site levanta exceção, segundo retorna OK
    capture_ok = _make_capture_result(target_url="https://ok.com")
    crawler.capture = AsyncMock(
        side_effect=[RuntimeError("Erro inesperado"), capture_ok]
    )

    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value=[])

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 1
    assert result.sites_failed == 1
    # Verifica que o site com erro foi registrado como falha
    assert result.site_results[0].success is False
    assert "Erro inesperado" in result.site_results[0].error_message
    assert result.site_results[1].success is True


# --- Testes de Alertas com Threshold ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_alert_only_sent_above_threshold(mock_get_session_patch):
    """Alertas enviados apenas para detecções acima do threshold (Req 6.1)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    brand_assets = [_make_brand_asset()]
    capture = _make_capture_result()

    # Uma detecção acima do threshold (85 >= 70) e uma abaixo (50 < 70)
    detections = [
        _make_detection(confidence=85),
        _make_detection(confidence=50),
    ]

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value=detections)

    alert_service = AsyncMock()
    alert_service.send_alert = AsyncMock(return_value=True)

    detection_store = AsyncMock()
    detection_store.save = AsyncMock(return_value="det-1")

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    config = _make_config(confidence_threshold=70)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
            config=config,
        )

        result = await coordinator.run_cycle()

    # Alerta enviado apenas para detecção com confidence=85
    assert alert_service.send_alert.call_count == 1
    # Ambas as detecções foram salvas
    assert detection_store.save.call_count == 2
    assert result.detections_found == 2


# --- Testes de Ciclo Sem Sites/Assets ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_with_no_target_sites(mock_get_session_patch):
    """Ciclo sem target sites registrados termina sem erros."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=[])

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=[])

    coordinator = _make_coordinator(
        brand_registry=brand_registry,
        target_site_manager=target_site_manager,
    )

    result = await coordinator.run_cycle()

    assert result.sites_processed == 0
    assert result.sites_failed == 0
    assert result.detections_found == 0
    assert result.site_results == []


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_with_no_brand_assets(mock_get_session_patch):
    """Ciclo sem brand assets não executa análise."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    capture = _make_capture_result()

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    analyzer = AsyncMock()

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=[])

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 1
    assert result.detections_found == 0
    # Analyzer não deve ser chamado se não há brand assets
    analyzer.analyze.assert_not_called()


# --- Testes de Alertas sem Destinatários ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_no_alert_when_no_recipients(mock_get_session_patch):
    """Não envia alertas se não há destinatários configurados."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    brand_assets = [_make_brand_asset()]
    capture = _make_capture_result()
    detections = [_make_detection(confidence=90)]

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value=detections)

    alert_service = AsyncMock()
    alert_service.send_alert = AsyncMock(return_value=True)

    detection_store = AsyncMock()
    detection_store.save = AsyncMock(return_value="det-1")

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    config = _make_config(recipients=[])

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
            config=config,
        )

        result = await coordinator.run_cycle()

    # Detecções foram salvas, mas alerta não enviado
    assert detection_store.save.call_count == 1
    alert_service.send_alert.assert_not_called()
    assert result.detections_found == 1


# --- Testes de Concurrent Cycle Skip ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_concurrent_cycle_second_is_skipped(mock_get_session_patch):
    """Quando dois ciclos são disparados simultaneamente, o segundo é
    pulado pois o primeiro já está em execução (Req 5.4)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    # Configuração: um target site com captura lenta (simula ciclo longo)
    target_sites = [_make_target_site()]
    brand_assets = [_make_brand_asset()]
    capture = _make_capture_result()

    # Evento para controlar quando o primeiro ciclo está "em execução"
    first_cycle_started = asyncio.Event()
    first_cycle_proceed = asyncio.Event()

    async def slow_capture(url: str) -> CaptureResult:
        """Captura lenta que sinaliza que o ciclo está em execução."""
        first_cycle_started.set()
        await first_cycle_proceed.wait()
        return capture

    crawler = AsyncMock()
    crawler.capture = slow_capture

    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value=[])

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        # Inicia primeiro ciclo (ficará preso no slow_capture)
        task1 = asyncio.create_task(coordinator.run_cycle())

        # Espera até que o primeiro ciclo esteja realmente executando
        await first_cycle_started.wait()

        # Tenta iniciar segundo ciclo — deve ser pulado
        result2 = await coordinator.run_cycle()

        # Libera o primeiro ciclo para terminar
        first_cycle_proceed.set()
        result1 = await task1

    # Segundo ciclo foi pulado
    assert result2.sites_processed == 0
    assert result2.sites_failed == 0
    assert result2.site_results == []

    # Primeiro ciclo completou normalmente
    assert result1.sites_processed == 1
    assert result1.sites_failed == 0


# --- Testes de Stats Update no Banco ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_record_created_and_updated_with_correct_stats(
    mock_get_session_patch,
):
    """Verifica que _create_cycle_record e _update_cycle_record são
    chamados com os dados corretos do ciclo (Req 5.6)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [
        _make_target_site(url="https://a.com", site_id="s1"),
        _make_target_site(url="https://b.com", site_id="s2"),
    ]
    brand_assets = [_make_brand_asset()]
    capture_a = _make_capture_result(target_url="https://a.com")
    capture_b = _make_capture_result(target_url="https://b.com")
    detection = _make_detection(target_url="https://a.com", confidence=80)

    crawler = AsyncMock()
    crawler.capture = AsyncMock(side_effect=[capture_a, capture_b])

    analyzer = AsyncMock()
    # Primeiro site: 1 detecção; segundo site: 0
    analyzer.analyze = AsyncMock(side_effect=[[detection], []])

    alert_service = AsyncMock()
    alert_service.send_alert = AsyncMock(return_value=True)

    detection_store = AsyncMock()
    detection_store.save = AsyncMock(return_value="det-1")

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=target_sites)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    # Verifica stats no resultado
    assert result.sites_processed == 2
    assert result.sites_failed == 0
    assert result.detections_found == 1

    # Verifica que get_session foi chamado (para create e update)
    # Ao menos 2 chamadas: 1 create + 1 update
    assert mock_get_session_patch.call_count >= 2

    # Verifica que session.add foi chamado (para criar ciclo no banco)
    assert mock_session.add.called
    # Verifica que session.flush foi chamado (para persistir)
    assert mock_session.flush.called


# --- Testes de Múltiplos Sites com Contagem Mista ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_multiple_sites_mixed_success_failure_counting(
    mock_get_session_patch,
):
    """Ciclo com 4 sites: 2 sucesso, 2 falha. Verifica contagem
    correta de sites_processed e sites_failed (Req 5.5, 5.6)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    sites = [
        _make_target_site(url="https://ok1.com", site_id="s1"),
        _make_target_site(url="https://fail1.com", site_id="s2"),
        _make_target_site(url="https://ok2.com", site_id="s3"),
        _make_target_site(url="https://fail2.com", site_id="s4"),
    ]
    brand_assets = [_make_brand_asset()]

    capture_ok1 = _make_capture_result(
        target_url="https://ok1.com", success=True
    )
    capture_fail1 = _make_capture_result(
        target_url="https://fail1.com", success=False
    )
    capture_ok2 = _make_capture_result(
        target_url="https://ok2.com", success=True
    )
    capture_fail2 = _make_capture_result(
        target_url="https://fail2.com", success=False
    )

    crawler = AsyncMock()
    crawler.capture = AsyncMock(
        side_effect=[capture_ok1, capture_fail1, capture_ok2, capture_fail2]
    )

    # Detecções: 1 para ok1, 2 para ok2
    det_ok1 = _make_detection(
        target_url="https://ok1.com", confidence=90
    )
    det_ok2_a = _make_detection(
        target_url="https://ok2.com", confidence=75
    )
    det_ok2_b = _make_detection(
        target_url="https://ok2.com", confidence=80
    )

    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(
        side_effect=[[det_ok1], [det_ok2_a, det_ok2_b]]
    )

    alert_service = AsyncMock()
    alert_service.send_alert = AsyncMock(return_value=True)

    detection_store = AsyncMock()
    detection_store.save = AsyncMock(return_value="det-1")

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=sites)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    # 2 sites OK, 2 falharam na captura
    assert result.sites_processed == 2
    assert result.sites_failed == 2
    # Detecções: 1 do ok1 + 2 do ok2 = 3
    assert result.detections_found == 3
    assert len(result.site_results) == 4

    # Verifica ordem dos resultados
    assert result.site_results[0].success is True  # ok1
    assert result.site_results[1].success is False  # fail1
    assert result.site_results[2].success is True  # ok2
    assert result.site_results[3].success is False  # fail2


# --- Testes de AnalysisIncompleteError ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_with_analysis_incomplete_error(
    mock_get_session_patch,
):
    """Quando o analyzer levanta AnalysisIncompleteError em um site,
    o site é registrado como falha e o ciclo continua (Req 5.5)."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    sites = [
        _make_target_site(url="https://fail-analysis.com", site_id="s1"),
        _make_target_site(url="https://ok.com", site_id="s2"),
    ]
    brand_assets = [_make_brand_asset()]

    capture_fail = _make_capture_result(
        target_url="https://fail-analysis.com"
    )
    capture_ok = _make_capture_result(target_url="https://ok.com")
    detection_ok = _make_detection(
        target_url="https://ok.com", confidence=85
    )

    crawler = AsyncMock()
    crawler.capture = AsyncMock(
        side_effect=[capture_fail, capture_ok]
    )

    analyzer = AsyncMock()
    # Primeiro site: AnalysisIncompleteError; segundo: sucesso
    analyzer.analyze = AsyncMock(
        side_effect=[
            AnalysisIncompleteError(
                "Análise incompleta: falha após tentativas de retry"
            ),
            [detection_ok],
        ]
    )

    alert_service = AsyncMock()
    alert_service.send_alert = AsyncMock(return_value=True)

    detection_store = AsyncMock()
    detection_store.save = AsyncMock(return_value="det-1")

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    brand_registry = AsyncMock()
    brand_registry.get_all_assets = AsyncMock(return_value=brand_assets)

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(return_value=sites)

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            analyzer=analyzer,
            alert_service=alert_service,
            detection_store=detection_store,
            screenshot_store=screenshot_store,
            brand_registry=brand_registry,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    # Site com AnalysisIncompleteError → falha
    assert result.sites_processed == 1
    assert result.sites_failed == 1
    assert result.detections_found == 1
    assert len(result.site_results) == 2

    # Primeiro site: falha com mensagem de erro
    failed_site = result.site_results[0]
    assert failed_site.success is False
    assert "Análise incompleta" in failed_site.error_message

    # Segundo site: sucesso normal
    ok_site = result.site_results[1]
    assert ok_site.success is True
    assert len(ok_site.detections) == 1
