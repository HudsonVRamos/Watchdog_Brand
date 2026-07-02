"""Property tests para completude do resultado de ciclo de monitoramento.

Valida que CycleResult contém todos os campos obrigatórios e contagens
corretas após ciclos com mix de sucesso e falha entre sites.

**Validates: Requirements 5.6**
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
    BoundingBox,
    CaptureResult,
    ComplianceReport,
    ComplianceRuleResult,
    CycleResult,
    DetectionResult,
    TargetSite,
)


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
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


def _success_mask_strategy(
    n: int,
) -> st.SearchStrategy[list[bool]]:
    """Gera máscara booleana de tamanho n indicando sucesso/falha por site."""
    return st.lists(
        st.booleans(),
        min_size=n,
        max_size=n,
    )


def _detections_count_strategy() -> st.SearchStrategy[int]:
    """Gera número de detecções para um site (0-5)."""
    return st.integers(min_value=0, max_value=5)


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


def _make_brand_asset() -> BrandAsset:
    """Cria um BrandAsset de teste."""
    return BrandAsset(
        id="asset-1",
        asset_type="logo",
        file_path=Path("/logos/brand.png"),
        text_value=None,
        content_hash="abc123",
        original_filename="brand.png",
        file_size_bytes=1024,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _make_detection(target_url: str, index: int = 0) -> DetectionResult:
    """Cria uma DetectionResult de teste."""
    return DetectionResult(
        target_url=target_url,
        match_type="logo" if index % 2 == 0 else "text",
        confidence=75,
        bounding_box=BoundingBox(10.0, 20.0, 30.0, 15.0),
        description=f"Detecção {index}",
        detected_at=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        screenshot_ref_id=f"ref-{index}",
    )


def _make_config() -> AppConfig:
    """Cria configuração de teste."""
    return AppConfig(
        crawler=CrawlerConfig(),
        analyzer=AnalyzerConfig(confidence_threshold=70),
        alert=AlertConfig(recipients=["admin@test.com"]),
        storage=StorageConfig(),
    )


def _make_capture_result(
    target_url: str, success: bool
) -> CaptureResult:
    """Cria CaptureResult conforme sucesso/falha."""
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


def _build_coordinator(
    target_sites: list[TargetSite],
    success_mask: list[bool],
    detections_per_site: list[int],
) -> MonitoringCoordinator:
    """Constrói MonitoringCoordinator com mocks configurados.

    Args:
        target_sites: Lista de sites-alvo.
        success_mask: Indica sucesso (True) ou falha (False) por site.
        detections_per_site: Número de detecções para cada site com
            sucesso (usado apenas para contagem no CycleResult).
    """
    # Configura capture side_effects baseado no success_mask
    capture_results = [
        _make_capture_result(site.url, success_mask[i])
        for i, site in enumerate(target_sites)
    ]

    crawler = AsyncMock()
    crawler.capture = AsyncMock(side_effect=capture_results)

    # ComplianceAnalyzer retorna report com detecções=0
    # (o novo coordinator não popula detections no SiteResult)
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


# --- Property Tests ---


class TestCycleResultCompleteness:
    """Property 12: Cycle Result Completeness.

    Ciclos com mix de sucesso/falha — o resultado contém todos os
    campos obrigatórios e contagens corretas.

    **Validates: Requirements 5.6**
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
        success_mask = data.draw(
            st.lists(st.booleans(), min_size=n, max_size=n)
        )

        n_success = sum(success_mask)
        detections_per_site = data.draw(
            st.lists(
                _detections_count_strategy(),
                min_size=n_success,
                max_size=n_success,
            )
        )

        fake_session_fn, _ = _mock_get_session()

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(Path, "read_bytes", return_value=b"png_data"),
        ):
            coordinator = _build_coordinator(
                target_sites, success_mask, detections_per_site
            )
            result = await coordinator.run_cycle()

        assert result.sites_processed + result.sites_failed == n

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_sites_processed_matches_success_count(
        self, data: st.DataObject
    ):
        """sites_processed == número de sites com sucesso."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)
        success_mask = data.draw(
            st.lists(st.booleans(), min_size=n, max_size=n)
        )

        n_success = sum(success_mask)
        detections_per_site = data.draw(
            st.lists(
                _detections_count_strategy(),
                min_size=n_success,
                max_size=n_success,
            )
        )

        fake_session_fn, _ = _mock_get_session()

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(Path, "read_bytes", return_value=b"png_data"),
        ):
            coordinator = _build_coordinator(
                target_sites, success_mask, detections_per_site
            )
            result = await coordinator.run_cycle()

        assert result.sites_processed == n_success

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_sites_failed_matches_failure_count(
        self, data: st.DataObject
    ):
        """sites_failed == número de sites que falharam."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)
        success_mask = data.draw(
            st.lists(st.booleans(), min_size=n, max_size=n)
        )

        n_success = sum(success_mask)
        n_failed = n - n_success
        detections_per_site = data.draw(
            st.lists(
                _detections_count_strategy(),
                min_size=n_success,
                max_size=n_success,
            )
        )

        fake_session_fn, _ = _mock_get_session()

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(Path, "read_bytes", return_value=b"png_data"),
        ):
            coordinator = _build_coordinator(
                target_sites, success_mask, detections_per_site
            )
            result = await coordinator.run_cycle()

        assert result.sites_failed == n_failed

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_site_results_has_one_entry_per_target(
        self, data: st.DataObject
    ):
        """site_results tem exatamente uma entrada por target site."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)
        success_mask = data.draw(
            st.lists(st.booleans(), min_size=n, max_size=n)
        )

        n_success = sum(success_mask)
        detections_per_site = data.draw(
            st.lists(
                _detections_count_strategy(),
                min_size=n_success,
                max_size=n_success,
            )
        )

        fake_session_fn, _ = _mock_get_session()

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(Path, "read_bytes", return_value=b"png_data"),
        ):
            coordinator = _build_coordinator(
                target_sites, success_mask, detections_per_site
            )
            result = await coordinator.run_cycle()

        assert len(result.site_results) == n

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_started_at_before_ended_at(
        self, data: st.DataObject
    ):
        """started_at <= ended_at para todo ciclo executado."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)
        success_mask = data.draw(
            st.lists(st.booleans(), min_size=n, max_size=n)
        )

        n_success = sum(success_mask)
        detections_per_site = data.draw(
            st.lists(
                _detections_count_strategy(),
                min_size=n_success,
                max_size=n_success,
            )
        )

        fake_session_fn, _ = _mock_get_session()

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(Path, "read_bytes", return_value=b"png_data"),
        ):
            coordinator = _build_coordinator(
                target_sites, success_mask, detections_per_site
            )
            result = await coordinator.run_cycle()

        assert result.started_at <= result.ended_at

    @_PBT_SETTINGS
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_detections_found_equals_sum_across_sites(
        self, data: st.DataObject
    ):
        """detections_found == soma de detecções em todos os sites."""
        target_sites = data.draw(_site_list_strategy())
        n = len(target_sites)
        success_mask = data.draw(
            st.lists(st.booleans(), min_size=n, max_size=n)
        )

        n_success = sum(success_mask)
        detections_per_site = data.draw(
            st.lists(
                _detections_count_strategy(),
                min_size=n_success,
                max_size=n_success,
            )
        )

        fake_session_fn, _ = _mock_get_session()

        with (
            patch(
                "brand_watchdog.coordinator.coordinator.get_session",
                side_effect=fake_session_fn,
            ),
            patch.object(Path, "read_bytes", return_value=b"png_data"),
        ):
            coordinator = _build_coordinator(
                target_sites, success_mask, detections_per_site
            )
            result = await coordinator.run_cycle()

        # No novo fluxo de compliance, detections no SiteResult
        # são sempre [] (violations persistidas internamente pelo
        # ComplianceAnalyzer). O total é a soma dos site_results.
        expected = sum(
            len(sr.detections) for sr in result.site_results
        )
        assert result.detections_found == expected
