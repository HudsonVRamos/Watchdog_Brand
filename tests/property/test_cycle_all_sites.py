"""Property tests para verificação de que o ciclo processa todos os sites.

Valida que o MonitoringCoordinator.run_cycle() processa TODOS os
Target Sites registrados em cada ciclo de monitoramento, sem
pular ou perder nenhum site.

**Validates: Requirements 5.3**
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


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=100,
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


def _make_config() -> AppConfig:
    """Cria configuração padrão para testes."""
    return AppConfig(
        crawler=CrawlerConfig(),
        analyzer=AnalyzerConfig(confidence_threshold=70),
        alert=AlertConfig(recipients=["admin@brand.com"]),
        storage=StorageConfig(),
    )


def _make_coordinator(
    crawler: AsyncMock,
    target_sites: list[TargetSite],
) -> MonitoringCoordinator:
    """Cria MonitoringCoordinator com mocks para sucesso total."""
    compliance_analyzer = AsyncMock()
    compliance_analyzer.analyze_compliance = AsyncMock(
        return_value=ComplianceReport(
            target_url="https://example.com",
            analyzed_at=datetime(
                2024, 6, 15, 10, 1, 0, tzinfo=timezone.utc
            ),
            overall_status="compliant",
            rule_results=[
                ComplianceRuleResult(
                    rule_id="facilitator_role",
                    status="PASS",
                    confidence=92,
                    description="OK",
                ),
            ],
            screenshot_ref_id="ref-123",
            cycle_id="cycle-1",
        )
    )

    compliance_notifier = AsyncMock()
    compliance_notifier.send_compliance_report = AsyncMock(
        return_value=True
    )

    detection_store = AsyncMock()
    detection_store.save = AsyncMock(return_value="det-1")

    screenshot_store = AsyncMock()
    screenshot_store.store = AsyncMock()

    target_site_manager = AsyncMock()
    target_site_manager.list_all = AsyncMock(
        return_value=target_sites
    )

    return MonitoringCoordinator(
        crawler=crawler,
        compliance_analyzer=compliance_analyzer,
        compliance_notifier=compliance_notifier,
        detection_store=detection_store,
        screenshot_store=screenshot_store,
        target_site_manager=target_site_manager,
        config=_make_config(),
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


class TestCycleProcessesAllSites:
    """Property 11: Cycle Processes All Sites.

    Conjuntos de 1-50 target sites (mockados), todos são processados
    pelo MonitoringCoordinator.run_cycle().

    **Validates: Requirements 5.3**
    """

    @_PBT_SETTINGS
    @given(target_sites=_target_sites_strategy())
    @pytest.mark.asyncio
    async def test_all_sites_processed_count(
        self, target_sites: list[TargetSite]
    ):
        """sites_processed + sites_failed == total de sites."""
        fake_session_fn, _ = _mock_get_session()

        crawler = AsyncMock()
        crawler.capture = AsyncMock(
            side_effect=lambda url: CaptureResult(
                target_url=url,
                screenshot_path=Path("/tmp/screenshot.png"),
                screenshot_ref_id="ref-123",
                captured_at=datetime(
                    2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc
                ),
                page_height_px=5000,
                was_truncated=False,
                success=True,
                error_message=None,
            )
        )

        coordinator = _make_coordinator(
            crawler=crawler,
            target_sites=target_sites,
        )

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(
                Path, "read_bytes", return_value=b"png_data"
            ),
        ):
            result = await coordinator.run_cycle()

        # Propriedade: todos os sites foram processados
        total = result.sites_processed + result.sites_failed
        assert total == len(target_sites)

    @_PBT_SETTINGS
    @given(target_sites=_target_sites_strategy())
    @pytest.mark.asyncio
    async def test_crawler_called_for_each_site(
        self, target_sites: list[TargetSite]
    ):
        """crawler.capture é chamado uma vez para cada site."""
        fake_session_fn, _ = _mock_get_session()

        crawler = AsyncMock()
        crawler.capture = AsyncMock(
            side_effect=lambda url: CaptureResult(
                target_url=url,
                screenshot_path=Path("/tmp/screenshot.png"),
                screenshot_ref_id="ref-123",
                captured_at=datetime(
                    2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc
                ),
                page_height_px=5000,
                was_truncated=False,
                success=True,
                error_message=None,
            )
        )

        coordinator = _make_coordinator(
            crawler=crawler,
            target_sites=target_sites,
        )

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(
                Path, "read_bytes", return_value=b"png_data"
            ),
        ):
            result = await coordinator.run_cycle()

        # Propriedade: crawler.capture chamado para cada site
        assert crawler.capture.call_count == len(target_sites)

        # Verifica que cada URL foi chamada
        called_urls = {
            call.args[0]
            for call in crawler.capture.call_args_list
        }
        expected_urls = {site.url for site in target_sites}
        assert called_urls == expected_urls

    @_PBT_SETTINGS
    @given(target_sites=_target_sites_strategy())
    @pytest.mark.asyncio
    async def test_site_results_matches_input_count(
        self, target_sites: list[TargetSite]
    ):
        """site_results contém exatamente um resultado por site."""
        fake_session_fn, _ = _mock_get_session()

        crawler = AsyncMock()
        crawler.capture = AsyncMock(
            side_effect=lambda url: CaptureResult(
                target_url=url,
                screenshot_path=Path("/tmp/screenshot.png"),
                screenshot_ref_id="ref-123",
                captured_at=datetime(
                    2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc
                ),
                page_height_px=5000,
                was_truncated=False,
                success=True,
                error_message=None,
            )
        )

        coordinator = _make_coordinator(
            crawler=crawler,
            target_sites=target_sites,
        )

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(
                Path, "read_bytes", return_value=b"png_data"
            ),
        ):
            result = await coordinator.run_cycle()

        # Propriedade: um SiteResult para cada TargetSite
        assert len(result.site_results) == len(target_sites)

    @_PBT_SETTINGS
    @given(target_sites=_target_sites_strategy())
    @pytest.mark.asyncio
    async def test_all_success_when_no_failures(
        self, target_sites: list[TargetSite]
    ):
        """Com mocks de sucesso, sites_processed == total, failed == 0."""
        fake_session_fn, _ = _mock_get_session()

        crawler = AsyncMock()
        crawler.capture = AsyncMock(
            side_effect=lambda url: CaptureResult(
                target_url=url,
                screenshot_path=Path("/tmp/screenshot.png"),
                screenshot_ref_id="ref-123",
                captured_at=datetime(
                    2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc
                ),
                page_height_px=5000,
                was_truncated=False,
                success=True,
                error_message=None,
            )
        )

        coordinator = _make_coordinator(
            crawler=crawler,
            target_sites=target_sites,
        )

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(
                Path, "read_bytes", return_value=b"png_data"
            ),
        ):
            result = await coordinator.run_cycle()

        # Propriedade: sem falhas mockadas => todos processados
        assert result.sites_processed == len(target_sites)
        assert result.sites_failed == 0
