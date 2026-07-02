"""Property test para serialização round-trip de ComplianceReport.

# Feature: mvp1-sky-amazon-compliance, Property 1: ComplianceReport serialization round-trip

**Validates: Requirements 7.5**

Para qualquer ComplianceReport válido (com rule_ids válidos, statuses em
{"PASS","FAIL","NOT_APPLICABLE"}, confidence 0-100, e description ≤ 1024 chars),
serializar para dict e deserializar de volta deve produzir um objeto
field-by-field idêntico.
"""

from __future__ import annotations

from hypothesis import given, settings

from brand_watchdog.models.dataclasses import ComplianceReport

from .strategies import compliance_report


_PBT_SETTINGS = settings(max_examples=100)


class TestComplianceReportSerializationRoundTrip:
    """Property 1: ComplianceReport serialization round-trip.

    **Validates: Requirements 7.5**
    """

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_to_dict_from_dict_produces_identical_object(
        self, report: ComplianceReport
    ) -> None:
        """Serializar → deserializar produz objeto field-by-field idêntico."""
        serialized = report.to_dict()
        deserialized = ComplianceReport.from_dict(serialized)

        # Verificar campos escalares
        assert deserialized.target_url == report.target_url, (
            f"target_url diverge: {deserialized.target_url!r} "
            f"!= {report.target_url!r}"
        )
        assert deserialized.analyzed_at == report.analyzed_at, (
            f"analyzed_at diverge: {deserialized.analyzed_at!r} "
            f"!= {report.analyzed_at!r}"
        )
        assert deserialized.overall_status == report.overall_status, (
            f"overall_status diverge: {deserialized.overall_status!r} "
            f"!= {report.overall_status!r}"
        )
        assert deserialized.screenshot_ref_id == report.screenshot_ref_id, (
            f"screenshot_ref_id diverge: {deserialized.screenshot_ref_id!r} "
            f"!= {report.screenshot_ref_id!r}"
        )
        assert deserialized.cycle_id == report.cycle_id, (
            f"cycle_id diverge: {deserialized.cycle_id!r} "
            f"!= {report.cycle_id!r}"
        )

        # Verificar lista de rule_results
        assert len(deserialized.rule_results) == len(report.rule_results), (
            f"rule_results len diverge: {len(deserialized.rule_results)} "
            f"!= {len(report.rule_results)}"
        )

        for i, (actual, expected) in enumerate(
            zip(deserialized.rule_results, report.rule_results)
        ):
            assert actual.rule_id == expected.rule_id, (
                f"rule_results[{i}].rule_id diverge: "
                f"{actual.rule_id!r} != {expected.rule_id!r}"
            )
            assert actual.status == expected.status, (
                f"rule_results[{i}].status diverge: "
                f"{actual.status!r} != {expected.status!r}"
            )
            assert actual.confidence == expected.confidence, (
                f"rule_results[{i}].confidence diverge: "
                f"{actual.confidence} != {expected.confidence}"
            )
            assert actual.description == expected.description, (
                f"rule_results[{i}].description diverge: "
                f"{actual.description!r} != {expected.description!r}"
            )

    @_PBT_SETTINGS
    @given(report=compliance_report())
    def test_serialized_analyzed_at_is_iso_format(
        self, report: ComplianceReport
    ) -> None:
        """O campo analyzed_at serializado deve ser uma string ISO 8601."""
        serialized = report.to_dict()
        analyzed_at_str = serialized["analyzed_at"]

        assert isinstance(analyzed_at_str, str), (
            f"analyzed_at deveria ser string, mas é {type(analyzed_at_str)}"
        )
        # Verificar que o isoformat round-trip funciona
        from datetime import datetime
        parsed = datetime.fromisoformat(analyzed_at_str)
        assert parsed == report.analyzed_at, (
            f"ISO parse diverge: {parsed!r} != {report.analyzed_at!r}"
        )
