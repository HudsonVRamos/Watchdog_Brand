"""Testes unitários para o MonitoringCoordinator (compliance).

Valida:
- Ciclo completo com sucesso (capture → analyze_compliance → notify)
- Lock de ciclo: pula execução se ciclo anterior em andamento
- Concurrent cycle skip: dois ciclos simultâneos, segundo é pulado
- Processamento de múltiplos sites com isolamento de falha
- Registro e atualização de MonitoringCycleModel no banco
- Stats update: create + update com dados corretos
- Múltiplos sites com contagem mista sucesso/falha
- Envio de email sempre (compliant ou non_compliant)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.config import (
    AlertConfig,
    AnalyzerConfig,
    AppConfig,
    CrawlerConfig,
    StorageConfig,
)
from brand_watchdog.coordinator.coordinator import MonitoringCoordinator
from brand_watchdog.models.dataclasses import (
    CaptureResult,
    ComplianceReport,
    ComplianceRuleResult,
    TargetSite,
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


def _make_compliance_report(
    target_url: str = "https://example.com",
    overall_status: str = "compliant",
    fail_count: int = 0,
) -> ComplianceReport:
    """Cria um ComplianceReport para testes."""
    rules = []
    rule_names = [
        "facilitator_role", "logo_application",
        "logo_effects", "content_separation",
        "naming_pricing", "kv_integrity",
    ]
    for i, name in enumerate(rule_names):
        if i < fail_count:
            rules.append(ComplianceRuleResult(
                rule_id=name,
                status="FAIL",
                confidence=85,
                description=f"Violação em {name}",
            ))
        else:
            rules.append(ComplianceRuleResult(
                rule_id=name,
                status="PASS",
                confidence=92,
                description=f"Regra {name} aprovada",
            ))
    return ComplianceReport(
        target_url=target_url,
        analyzed_at=datetime(2024, 6, 15, 10, 1, 0, tzinfo=timezone.utc),
        overall_status=overall_status,
        rule_results=rules,
        screenshot_ref_id="ref-123",
        cycle_id="cycle-1",
    )


def _make_config(
    recipients: list[str] | None = None,
) -> AppConfig:
    if recipients is None:
        recipients = ["admin@brand.com"]
    return AppConfig(
        crawler=CrawlerConfig(),
        analyzer=AnalyzerConfig(),
        alert=AlertConfig(recipients=recipients),
        storage=StorageConfig(),
    )


def _make_coordinator(
    crawler: AsyncMock | None = None,
    compliance_analyzer: AsyncMock | None = None,
    compliance_notifier: AsyncMock | None = None,
    detection_store: AsyncMock | None = None,
    screenshot_store: AsyncMock | None = None,
    target_site_manager: AsyncMock | None = None,
    config: AppConfig | None = None,
) -> MonitoringCoordinator:
    """Cria um MonitoringCoordinator com mocks para testes."""
    _crawler = crawler or AsyncMock()
    _compliance_analyzer = compliance_analyzer or AsyncMock()
    _compliance_notifier = compliance_notifier or AsyncMock()
    _detection_store = detection_store or AsyncMock()
    _screenshot_store = screenshot_store or AsyncMock()
    _target_site_manager = target_site_manager or AsyncMock()
    _config = config or _make_config()

    return MonitoringCoordinator(
        crawler=_crawler,
        compliance_analyzer=_compliance_analyzer,
        compliance_notifier=_compliance_notifier,
        detection_store=_detection_store,
        screenshot_store=_screenshot_store,
        target_site_manager=_target_site_manager,
        config=_config,
    )


# --- Testes de Ciclo Completo ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_run_cycle_success(mock_get_session_patch):
    """Ciclo completo processa site com sucesso via compliance."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    capture = _make_capture_result()
    report = _make_compliance_report()

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 1
    assert result.sites_failed == 0
    assert len(result.site_results) == 1
    assert result.site_results[0].success is True
    crawler.capture.assert_called_once_with("https://example.com")
    compliance_analyzer.analyze_compliance.assert_called_once()
    compliance_notifier.send_compliance_report.assert_called_once()


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


# --- Testes de Falha Isolada por Site ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_site_failure_does_not_stop_cycle(mock_get_session_patch):
    """Falha em um site não interrompe processamento dos demais."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    site_ok = _make_target_site(url="https://ok.com", site_id="s1")
    site_fail = _make_target_site(
        url="https://fail.com", site_id="s2"
    )
    target_sites = [site_fail, site_ok]

    capture_ok = _make_capture_result(target_url="https://ok.com")
    capture_fail = _make_capture_result(
        target_url="https://fail.com", success=False
    )
    report = _make_compliance_report(target_url="https://ok.com")

    crawler = AsyncMock()
    crawler.capture = AsyncMock(
        side_effect=[capture_fail, capture_ok]
    )

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 1
    assert result.sites_failed == 1
    assert len(result.site_results) == 2


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_site_exception_is_isolated(mock_get_session_patch):
    """Exceção em um site é capturada sem afetar os demais."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    site1 = _make_target_site(url="https://error.com", site_id="s1")
    site2 = _make_target_site(url="https://ok.com", site_id="s2")
    target_sites = [site1, site2]
    report = _make_compliance_report(target_url="https://ok.com")

    crawler = AsyncMock()
    capture_ok = _make_capture_result(target_url="https://ok.com")
    crawler.capture = AsyncMock(
        side_effect=[RuntimeError("Erro inesperado"), capture_ok]
    )

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 1
    assert result.sites_failed == 1
    assert result.site_results[0].success is False
    assert "Erro inesperado" in result.site_results[0].error_message
    assert result.site_results[1].success is True


# --- Testes de Envio de Email Sempre ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_email_sent_for_compliant_report(mock_get_session_patch):
    """Email é enviado mesmo quando relatório é compliant."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    capture = _make_capture_result()
    report = _make_compliance_report(overall_status="compliant")

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        await coordinator.run_cycle()

    compliance_notifier.send_compliance_report.assert_called_once()


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_email_sent_for_non_compliant_report(
    mock_get_session_patch,
):
    """Email é enviado quando relatório é non_compliant."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    capture = _make_capture_result()
    report = _make_compliance_report(
        overall_status="non_compliant", fail_count=2
    )

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        await coordinator.run_cycle()

    compliance_notifier.send_compliance_report.assert_called_once()


# --- Testes Sem Destinatários ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_no_email_when_no_recipients(mock_get_session_patch):
    """Não envia email se não há destinatários configurados."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [_make_target_site()]
    capture = _make_capture_result()
    report = _make_compliance_report()

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    config = _make_config(recipients=[])

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
            config=config,
        )

        await coordinator.run_cycle()

    compliance_notifier.send_compliance_report.assert_not_called()


# --- Testes de Ciclo Sem Sites ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_with_no_target_sites(mock_get_session_patch):
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
    capture = _make_capture_result()
    report = _make_compliance_report()

    first_cycle_started = asyncio.Event()
    first_cycle_proceed = asyncio.Event()

    async def slow_capture(url: str) -> CaptureResult:
        """Captura lenta que sinaliza ciclo em execução."""
        first_cycle_started.set()
        await first_cycle_proceed.wait()
        return capture

    crawler = AsyncMock()
    crawler.capture = slow_capture

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        task1 = asyncio.create_task(coordinator.run_cycle())
        await first_cycle_started.wait()

        result2 = await coordinator.run_cycle()

        first_cycle_proceed.set()
        result1 = await task1

    assert result2.sites_processed == 0
    assert result2.site_results == []
    assert result1.sites_processed == 1


# --- Teste de Stats no Banco ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_cycle_record_created_and_updated(mock_get_session_patch):
    """Verifica que _create_cycle_record e _update_cycle_record são
    chamados corretamente."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    target_sites = [
        _make_target_site(url="https://a.com", site_id="s1"),
        _make_target_site(url="https://b.com", site_id="s2"),
    ]
    capture_a = _make_capture_result(target_url="https://a.com")
    capture_b = _make_capture_result(target_url="https://b.com")
    report = _make_compliance_report()

    crawler = AsyncMock()
    crawler.capture = AsyncMock(
        side_effect=[capture_a, capture_b]
    )

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 2
    assert result.sites_failed == 0
    # get_session chamado para create + update do ciclo
    assert mock_get_session_patch.call_count >= 2
    assert mock_session.add.called
    assert mock_session.flush.called


# --- Testes de Brand Per-Site ---


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_site_brand_passed_to_analyzer(mock_get_session_patch):
    """Verifica que o brand do site é passado para analyze_compliance."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    site_sky = _make_target_site(
        url="https://sky.com", site_id="s1", brand="sky_plus"
    )
    site_dgo = _make_target_site(
        url="https://dgo.com", site_id="s2", brand="dgo"
    )
    target_sites = [site_sky, site_dgo]

    capture_sky = _make_capture_result(target_url="https://sky.com")
    capture_dgo = _make_capture_result(target_url="https://dgo.com")
    report = _make_compliance_report()

    crawler = AsyncMock()
    crawler.capture = AsyncMock(
        side_effect=[capture_sky, capture_dgo]
    )

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        result = await coordinator.run_cycle()

    assert result.sites_processed == 2
    assert result.sites_failed == 0

    # Verifica que analyze_compliance foi chamado com brand correto
    calls = compliance_analyzer.analyze_compliance.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs.get("brand") == "sky_plus"
    assert calls[1].kwargs.get("brand") == "dgo"


@pytest.mark.asyncio
@patch("brand_watchdog.coordinator.coordinator.get_session")
async def test_default_brand_is_sky_plus(mock_get_session_patch):
    """Sites sem brand explícito usam sky_plus como padrão."""
    fake_session_fn, mock_session = _mock_get_session()
    mock_get_session_patch.side_effect = fake_session_fn

    # TargetSite sem brand explícito (usa default)
    site = _make_target_site()
    target_sites = [site]

    capture = _make_capture_result()
    report = _make_compliance_report()

    crawler = AsyncMock()
    crawler.capture = AsyncMock(return_value=capture)

    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=report
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    with patch.object(Path, "read_bytes", return_value=b"png_data"):
        coordinator = _make_coordinator(
            crawler=crawler,
            compliance_analyzer=compliance_analyzer,
            compliance_notifier=compliance_notifier,
            screenshot_store=screenshot_store,
            target_site_manager=target_site_manager,
        )

        await coordinator.run_cycle()

    call_kwargs = (
        compliance_analyzer.analyze_compliance.call_args.kwargs
    )
    assert call_kwargs.get("brand") == "sky_plus"
