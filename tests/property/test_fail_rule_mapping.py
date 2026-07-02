"""Property tests para mapeamento FAIL → DetectionResult.

# Feature: mvp1-sky-amazon-compliance, Property 5: FAIL rule mapping to DetectionResult

**Validates: Requirements 9.3, 9.5**

Property 5: Para qualquer ComplianceRuleResult com status "FAIL", o sistema
deve produzir um DetectionResult onde: match_type == rule_id, confidence == rule.confidence,
bounding box com todas coordenadas 0.0, description == rule.description,
detected_at == report.analyzed_at, screenshot_ref_id == report.screenshot_ref_id,
e expires_at == analyzed_at + timedelta(days=detection_retention_days).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.analyzer.compliance_analyzer import ComplianceAnalyzer
from brand_watchdog.config import AnalyzerConfig, StorageConfig
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    ComplianceReport,
    ComplianceRuleResult,
    DetectionResult,
)

from tests.property.strategies import (
    analyzed_at_datetime,
    compliance_rule_result,
)


_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@st.composite
def _fail_rule_with_report_context(
    draw: st.DrawFn,
) -> tuple[ComplianceRuleResult, ComplianceReport, int]:
    """Gera um ComplianceRuleResult com status FAIL, um ComplianceReport
    contendo essa regra, e um valor de detection_retention_days."""
    # Gera a regra FAIL
    fail_rule = draw(compliance_rule_result(status="FAIL"))

    # Gera context do report
    analyzed_at = draw(analyzed_at_datetime())
    target_url = draw(
        st.from_regex(
            r"https://[a-z]{3,10}\.[a-z]{2,5}/[a-z]{1,10}",
            fullmatch=True,
        )
    )
    screenshot_ref_id = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=5,
            max_size=30,
        )
    )
    cycle_id = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=5,
            max_size=30,
        )
    )

    # Cria report com a regra FAIL
    report = ComplianceReport(
        target_url=target_url,
        analyzed_at=analyzed_at,
        overall_status="non_compliant",
        rule_results=[fail_rule],
        screenshot_ref_id=screenshot_ref_id,
        cycle_id=cycle_id,
    )

    # Gera retention_days entre 1 e 365
    retention_days = draw(st.integers(min_value=1, max_value=365))

    return (fail_rule, report, retention_days)


class TestFailRuleMappingToDetectionResult:
    """Property 5: FAIL rule mapping to DetectionResult.

    Para qualquer ComplianceRuleResult com status FAIL, o DetectionResult
    produzido deve ter mapeamento correto de todos os campos.

    **Validates: Requirements 9.3, 9.5**
    """

    @_PBT_SETTINGS
    @given(data=_fail_rule_with_report_context())
    def test_fail_rule_maps_match_type_to_rule_id(
        self,
        data: tuple[ComplianceRuleResult, ComplianceReport, int],
    ):
        """match_type do DetectionResult deve ser igual ao rule_id da regra FAIL."""
        fail_rule, report, retention_days = data

        # Configura mocks
        saved_detections: list[DetectionResult] = []

        async def mock_save(
            detection: DetectionResult,
            target_site_id: str | None = None,
            monitoring_cycle_id: str = "",
        ) -> str:
            saved_detections.append(detection)
            return "test-id"

        detection_store = MagicMock()
        detection_store.save = AsyncMock(side_effect=mock_save)

        storage_config = StorageConfig()
        storage_config.detection_retention_days = retention_days

        analyzer = ComplianceAnalyzer(
            config=AnalyzerConfig(),
            bedrock_client=MagicMock(),
            prompt_builder=MagicMock(),
            detection_store=detection_store,
            storage_config=storage_config,
        )

        # Executa persistência
        asyncio.run(
            analyzer._persist_violations(
                report=report,
                target_url=report.target_url,
                screenshot_ref_id=report.screenshot_ref_id,
                cycle_id=report.cycle_id,
            )
        )

        # Verifica mapeamento
        assert len(saved_detections) == 1
        detection = saved_detections[0]
        assert detection.match_type == fail_rule.rule_id, (
            f"match_type '{detection.match_type}' != rule_id '{fail_rule.rule_id}'"
        )

    @_PBT_SETTINGS
    @given(data=_fail_rule_with_report_context())
    def test_fail_rule_maps_confidence_correctly(
        self,
        data: tuple[ComplianceRuleResult, ComplianceReport, int],
    ):
        """confidence do DetectionResult deve ser igual ao confidence da regra FAIL."""
        fail_rule, report, retention_days = data

        saved_detections: list[DetectionResult] = []

        async def mock_save(
            detection: DetectionResult,
            target_site_id: str | None = None,
            monitoring_cycle_id: str = "",
        ) -> str:
            saved_detections.append(detection)
            return "test-id"

        detection_store = MagicMock()
        detection_store.save = AsyncMock(side_effect=mock_save)

        storage_config = StorageConfig()
        storage_config.detection_retention_days = retention_days

        analyzer = ComplianceAnalyzer(
            config=AnalyzerConfig(),
            bedrock_client=MagicMock(),
            prompt_builder=MagicMock(),
            detection_store=detection_store,
            storage_config=storage_config,
        )

        asyncio.run(
            analyzer._persist_violations(
                report=report,
                target_url=report.target_url,
                screenshot_ref_id=report.screenshot_ref_id,
                cycle_id=report.cycle_id,
            )
        )

        assert len(saved_detections) == 1
        detection = saved_detections[0]
        assert detection.confidence == fail_rule.confidence, (
            f"confidence {detection.confidence} != {fail_rule.confidence}"
        )

    @_PBT_SETTINGS
    @given(data=_fail_rule_with_report_context())
    def test_fail_rule_maps_bounding_box_to_zeros(
        self,
        data: tuple[ComplianceRuleResult, ComplianceReport, int],
    ):
        """bounding_box do DetectionResult deve ter todas coordenadas 0.0."""
        fail_rule, report, retention_days = data

        saved_detections: list[DetectionResult] = []

        async def mock_save(
            detection: DetectionResult,
            target_site_id: str | None = None,
            monitoring_cycle_id: str = "",
        ) -> str:
            saved_detections.append(detection)
            return "test-id"

        detection_store = MagicMock()
        detection_store.save = AsyncMock(side_effect=mock_save)

        storage_config = StorageConfig()
        storage_config.detection_retention_days = retention_days

        analyzer = ComplianceAnalyzer(
            config=AnalyzerConfig(),
            bedrock_client=MagicMock(),
            prompt_builder=MagicMock(),
            detection_store=detection_store,
            storage_config=storage_config,
        )

        asyncio.run(
            analyzer._persist_violations(
                report=report,
                target_url=report.target_url,
                screenshot_ref_id=report.screenshot_ref_id,
                cycle_id=report.cycle_id,
            )
        )

        assert len(saved_detections) == 1
        bbox = saved_detections[0].bounding_box
        expected_bbox = BoundingBox(
            x_percent=0.0,
            y_percent=0.0,
            width_percent=0.0,
            height_percent=0.0,
        )
        assert bbox.x_percent == expected_bbox.x_percent, (
            f"bbox.x_percent {bbox.x_percent} != 0.0"
        )
        assert bbox.y_percent == expected_bbox.y_percent, (
            f"bbox.y_percent {bbox.y_percent} != 0.0"
        )
        assert bbox.width_percent == expected_bbox.width_percent, (
            f"bbox.width_percent {bbox.width_percent} != 0.0"
        )
        assert bbox.height_percent == expected_bbox.height_percent, (
            f"bbox.height_percent {bbox.height_percent} != 0.0"
        )

    @_PBT_SETTINGS
    @given(data=_fail_rule_with_report_context())
    def test_fail_rule_maps_description_correctly(
        self,
        data: tuple[ComplianceRuleResult, ComplianceReport, int],
    ):
        """description do DetectionResult deve ser igual à description da regra FAIL."""
        fail_rule, report, retention_days = data

        saved_detections: list[DetectionResult] = []

        async def mock_save(
            detection: DetectionResult,
            target_site_id: str | None = None,
            monitoring_cycle_id: str = "",
        ) -> str:
            saved_detections.append(detection)
            return "test-id"

        detection_store = MagicMock()
        detection_store.save = AsyncMock(side_effect=mock_save)

        storage_config = StorageConfig()
        storage_config.detection_retention_days = retention_days

        analyzer = ComplianceAnalyzer(
            config=AnalyzerConfig(),
            bedrock_client=MagicMock(),
            prompt_builder=MagicMock(),
            detection_store=detection_store,
            storage_config=storage_config,
        )

        asyncio.run(
            analyzer._persist_violations(
                report=report,
                target_url=report.target_url,
                screenshot_ref_id=report.screenshot_ref_id,
                cycle_id=report.cycle_id,
            )
        )

        assert len(saved_detections) == 1
        detection = saved_detections[0]
        assert detection.description == fail_rule.description, (
            f"description '{detection.description}' != '{fail_rule.description}'"
        )

    @_PBT_SETTINGS
    @given(data=_fail_rule_with_report_context())
    def test_fail_rule_maps_detected_at_to_analyzed_at(
        self,
        data: tuple[ComplianceRuleResult, ComplianceReport, int],
    ):
        """detected_at do DetectionResult deve ser igual ao analyzed_at do report."""
        fail_rule, report, retention_days = data

        saved_detections: list[DetectionResult] = []

        async def mock_save(
            detection: DetectionResult,
            target_site_id: str | None = None,
            monitoring_cycle_id: str = "",
        ) -> str:
            saved_detections.append(detection)
            return "test-id"

        detection_store = MagicMock()
        detection_store.save = AsyncMock(side_effect=mock_save)

        storage_config = StorageConfig()
        storage_config.detection_retention_days = retention_days

        analyzer = ComplianceAnalyzer(
            config=AnalyzerConfig(),
            bedrock_client=MagicMock(),
            prompt_builder=MagicMock(),
            detection_store=detection_store,
            storage_config=storage_config,
        )

        asyncio.run(
            analyzer._persist_violations(
                report=report,
                target_url=report.target_url,
                screenshot_ref_id=report.screenshot_ref_id,
                cycle_id=report.cycle_id,
            )
        )

        assert len(saved_detections) == 1
        detection = saved_detections[0]
        assert detection.detected_at == report.analyzed_at, (
            f"detected_at {detection.detected_at} != analyzed_at {report.analyzed_at}"
        )

    @_PBT_SETTINGS
    @given(data=_fail_rule_with_report_context())
    def test_fail_rule_maps_screenshot_ref_id(
        self,
        data: tuple[ComplianceRuleResult, ComplianceReport, int],
    ):
        """screenshot_ref_id do DetectionResult deve ser o do report."""
        fail_rule, report, retention_days = data

        saved_detections: list[DetectionResult] = []

        async def mock_save(
            detection: DetectionResult,
            target_site_id: str | None = None,
            monitoring_cycle_id: str = "",
        ) -> str:
            saved_detections.append(detection)
            return "test-id"

        detection_store = MagicMock()
        detection_store.save = AsyncMock(side_effect=mock_save)

        storage_config = StorageConfig()
        storage_config.detection_retention_days = retention_days

        analyzer = ComplianceAnalyzer(
            config=AnalyzerConfig(),
            bedrock_client=MagicMock(),
            prompt_builder=MagicMock(),
            detection_store=detection_store,
            storage_config=storage_config,
        )

        asyncio.run(
            analyzer._persist_violations(
                report=report,
                target_url=report.target_url,
                screenshot_ref_id=report.screenshot_ref_id,
                cycle_id=report.cycle_id,
            )
        )

        assert len(saved_detections) == 1
        detection = saved_detections[0]
        assert detection.screenshot_ref_id == report.screenshot_ref_id, (
            f"screenshot_ref_id '{detection.screenshot_ref_id}' != "
            f"'{report.screenshot_ref_id}'"
        )

    @_PBT_SETTINGS
    @given(data=_fail_rule_with_report_context())
    def test_fail_rule_expires_at_uses_retention_days(
        self,
        data: tuple[ComplianceRuleResult, ComplianceReport, int],
    ):
        """O DetectionStore.save é chamado com o detection que tem detected_at
        correto, e o store calcula expires_at = detected_at + retention_days.

        Como o ComplianceAnalyzer passa retention_days para _save_with_retry
        e o DetectionStore.save calcula expires_at internamente, verificamos
        que o detected_at está correto (o que garante expires_at correto
        quando combined com retention_days no store).

        Adicionalmente, verificamos que o _save_with_retry recebe o
        retention_days correto da storage_config.
        """
        fail_rule, report, retention_days = data

        # Captura os argumentos passados a _save_with_retry
        save_calls: list[tuple[DetectionResult, str, str, int]] = []

        original_save_with_retry = ComplianceAnalyzer._save_with_retry

        async def mock_save_with_retry(
            self_arg,
            detection: DetectionResult,
            target_url: str,
            cycle_id: str,
            ret_days: int,
        ) -> None:
            save_calls.append((detection, target_url, cycle_id, ret_days))

        storage_config = StorageConfig()
        storage_config.detection_retention_days = retention_days

        analyzer = ComplianceAnalyzer(
            config=AnalyzerConfig(),
            bedrock_client=MagicMock(),
            prompt_builder=MagicMock(),
            detection_store=MagicMock(),
            storage_config=storage_config,
        )

        # Monkey-patch _save_with_retry para capturar chamadas
        import types
        analyzer._save_with_retry = types.MethodType(  # type: ignore[assignment]
            lambda self, det, url, cid, rd: mock_save_with_retry(
                self, det, url, cid, rd
            ),
            analyzer,
        )

        asyncio.run(
            analyzer._persist_violations(
                report=report,
                target_url=report.target_url,
                screenshot_ref_id=report.screenshot_ref_id,
                cycle_id=report.cycle_id,
            )
        )

        assert len(save_calls) == 1
        detection, _, _, actual_retention_days = save_calls[0]

        # Verifica que retention_days passado é o configurado
        assert actual_retention_days == retention_days, (
            f"retention_days {actual_retention_days} != configurado {retention_days}"
        )

        # Verifica que detected_at do detection é analyzed_at do report
        # (garante que expires_at = analyzed_at + retention_days será correto)
        assert detection.detected_at == report.analyzed_at, (
            f"detected_at {detection.detected_at} != analyzed_at {report.analyzed_at}"
        )

        # Calcula o expires_at esperado para documentar a propriedade
        expected_expires_at = report.analyzed_at + timedelta(days=retention_days)
        # O expires_at real é calculado dentro do DetectionStore.save,
        # mas a combinação de detected_at correto + retention_days correto
        # garante que expires_at == analyzed_at + timedelta(days=retention_days)
        assert expected_expires_at == detection.detected_at + timedelta(
            days=actual_retention_days
        )
