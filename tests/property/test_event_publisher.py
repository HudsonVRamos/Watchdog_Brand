"""Property test para completude do evento ComplianceCompleted no EventBridge.

# Feature: architecture-evolution, Property 7: Completude do Evento ComplianceCompleted

**Validates: Requirements 5.1, 5.2, 5.3**

Para qualquer resultado de análise de compliance concluída com sucesso,
o evento publicado no EventBridge SHALL conter source="brand-watchdog",
detail-type="ComplianceCompleted", e no campo "detail": site_id (UUID),
cycle_id (UUID), target_url (string), brand ("sky_plus"|"dgo"),
overall_status ("compliant"|"non_compliant"), rule_results (lista de
exatamente 6 elementos com rule_id, status e confidence),
screenshot_s3_key (string), analyzed_at (timestamp ISO 8601 UTC).
"""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from brand_watchdog.events.models import ComplianceCompletedEvent
from brand_watchdog.events.publisher import EventPublisher


_PBT_SETTINGS = settings(max_examples=30)

# -- Regex para validação de UUID e ISO 8601 UTC --

_UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

_ISO8601_UTC_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)$"
)

# -- Estratégias de geração de dados --

_uuid_strategy = st.uuids().map(str)

_brand_strategy = st.sampled_from(["sky_plus", "dgo"])

_overall_status_strategy = st.sampled_from(["compliant", "non_compliant"])

_url_strategy = st.from_regex(
    r"https://[a-z]{3,20}\.[a-z]{2,5}(/[a-z0-9_\-]{1,50}){0,5}",
    fullmatch=True,
).filter(lambda u: len(u) <= 2048)

_rule_status_strategy = st.sampled_from(["PASS", "FAIL", "NOT_APPLICABLE"])

_confidence_strategy = st.integers(min_value=0, max_value=100)

# Gera exatamente 6 regras com rule_id únicos
_RULE_IDS = [
    "RULE_001",
    "RULE_002",
    "RULE_003",
    "RULE_004",
    "RULE_005",
    "RULE_006",
]


@st.composite
def rule_result_strategy(draw: st.DrawFn, rule_id: str) -> dict:
    """Gera um dict de resultado de regra com rule_id fixo."""
    return {
        "rule_id": rule_id,
        "status": draw(_rule_status_strategy),
        "confidence": draw(_confidence_strategy),
    }


@st.composite
def rule_results_strategy(draw: st.DrawFn) -> list[dict]:
    """Gera lista de exatamente 6 resultados de regra."""
    return [draw(rule_result_strategy(rule_id)) for rule_id in _RULE_IDS]


@st.composite
def iso8601_utc_strategy(draw: st.DrawFn) -> str:
    """Gera um timestamp ISO 8601 UTC válido."""
    year = draw(st.integers(min_value=2020, max_value=2030))
    month = draw(st.integers(min_value=1, max_value=12))
    day = draw(st.integers(min_value=1, max_value=28))
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    second = draw(st.integers(min_value=0, max_value=59))
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+00:00"


@st.composite
def screenshot_s3_key_strategy(draw: st.DrawFn, cycle_id: str) -> str:
    """Gera chave S3 no formato screenshots/{cycle_id}/{uuid}.png."""
    screenshot_uuid = draw(_uuid_strategy)
    return f"screenshots/{cycle_id}/{screenshot_uuid}.png"


@st.composite
def compliance_completed_event_strategy(
    draw: st.DrawFn,
) -> ComplianceCompletedEvent:
    """Gera um ComplianceCompletedEvent válido para publicação."""
    site_id = draw(_uuid_strategy)
    cycle_id = draw(_uuid_strategy)
    target_url = draw(_url_strategy)
    brand = draw(_brand_strategy)
    overall_status = draw(_overall_status_strategy)
    rule_results = draw(rule_results_strategy())
    screenshot_s3_key = draw(screenshot_s3_key_strategy(cycle_id))
    analyzed_at = draw(iso8601_utc_strategy())

    return ComplianceCompletedEvent(
        site_id=site_id,
        cycle_id=cycle_id,
        target_url=target_url,
        brand=brand,
        overall_status=overall_status,
        rule_results=rule_results,
        screenshot_s3_key=screenshot_s3_key,
        analyzed_at=analyzed_at,
    )


def _create_success_response() -> dict:
    """Cria resposta EventBridge simulando sucesso."""
    return {
        "FailedEntryCount": 0,
        "Entries": [{"EventId": "mock-event-id-12345"}],
    }


class TestEventPublisherCompleteness:
    """Property 7: Completude do Evento ComplianceCompleted.

    **Validates: Requirements 5.1, 5.2, 5.3**
    """

    @_PBT_SETTINGS
    @given(event=compliance_completed_event_strategy())
    def test_event_has_correct_source_and_detail_type(
        self, event: ComplianceCompletedEvent
    ) -> None:
        """O evento publicado SHALL ter source='brand-watchdog' e detail-type='ComplianceCompleted'.

        Verifica que o PutEvents é chamado com os campos de envelope
        corretos conforme especificação do EventBridge.
        """
        with patch("brand_watchdog.events.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.put_events.return_value = _create_success_response()

            publisher = EventPublisher()
            result = asyncio.run(
                publisher.publish_compliance_completed(event)
            )

            assert result is True, "Publicação deveria ter sucesso com mock"

            # Verificar chamada ao PutEvents
            mock_client.put_events.assert_called_once()
            call_kwargs = mock_client.put_events.call_args[1]
            entries = call_kwargs["Entries"]

            assert len(entries) == 1, "Deve haver exatamente 1 entrada"

            entry = entries[0]
            assert entry["Source"] == "brand-watchdog", (
                f"Source deveria ser 'brand-watchdog', obtido: {entry['Source']}"
            )
            assert entry["DetailType"] == "ComplianceCompleted", (
                f"DetailType deveria ser 'ComplianceCompleted', "
                f"obtido: {entry['DetailType']}"
            )

    @_PBT_SETTINGS
    @given(event=compliance_completed_event_strategy())
    def test_event_detail_contains_all_required_fields(
        self, event: ComplianceCompletedEvent
    ) -> None:
        """O campo 'detail' SHALL conter todos os 8 campos obrigatórios.

        Campos obrigatórios: site_id, cycle_id, target_url, brand,
        overall_status, rule_results, screenshot_s3_key, analyzed_at.
        """
        required_fields = {
            "site_id",
            "cycle_id",
            "target_url",
            "brand",
            "overall_status",
            "rule_results",
            "screenshot_s3_key",
            "analyzed_at",
        }

        with patch("brand_watchdog.events.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.put_events.return_value = _create_success_response()

            publisher = EventPublisher()
            result = asyncio.run(
                publisher.publish_compliance_completed(event)
            )

            assert result is True

            call_kwargs = mock_client.put_events.call_args[1]
            detail_json = call_kwargs["Entries"][0]["Detail"]
            detail = json.loads(detail_json)

            # Verificar presença de todos os campos obrigatórios
            missing = required_fields - set(detail.keys())
            assert not missing, (
                f"Campos obrigatórios ausentes no detail: {sorted(missing)}"
            )

    @_PBT_SETTINGS
    @given(event=compliance_completed_event_strategy())
    def test_event_detail_preserves_field_values(
        self, event: ComplianceCompletedEvent
    ) -> None:
        """Os valores no 'detail' SHALL corresponder exatamente ao evento original.

        Verifica que site_id, cycle_id, target_url, brand, overall_status,
        screenshot_s3_key e analyzed_at são preservados sem alteração.
        """
        with patch("brand_watchdog.events.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.put_events.return_value = _create_success_response()

            publisher = EventPublisher()
            result = asyncio.run(
                publisher.publish_compliance_completed(event)
            )

            assert result is True

            call_kwargs = mock_client.put_events.call_args[1]
            detail_json = call_kwargs["Entries"][0]["Detail"]
            detail = json.loads(detail_json)

            # Verificar valores escalares
            assert detail["site_id"] == event.site_id, (
                f"site_id diverge: esperado={event.site_id}, obtido={detail['site_id']}"
            )
            assert detail["cycle_id"] == event.cycle_id, (
                f"cycle_id diverge: esperado={event.cycle_id}, obtido={detail['cycle_id']}"
            )
            assert detail["target_url"] == event.target_url, (
                f"target_url diverge: esperado={event.target_url}, "
                f"obtido={detail['target_url']}"
            )
            assert detail["brand"] == event.brand, (
                f"brand diverge: esperado={event.brand}, obtido={detail['brand']}"
            )
            assert detail["overall_status"] == event.overall_status, (
                f"overall_status diverge: esperado={event.overall_status}, "
                f"obtido={detail['overall_status']}"
            )
            assert detail["screenshot_s3_key"] == event.screenshot_s3_key, (
                f"screenshot_s3_key diverge: esperado={event.screenshot_s3_key}, "
                f"obtido={detail['screenshot_s3_key']}"
            )
            assert detail["analyzed_at"] == event.analyzed_at, (
                f"analyzed_at diverge: esperado={event.analyzed_at}, "
                f"obtido={detail['analyzed_at']}"
            )

    @_PBT_SETTINGS
    @given(event=compliance_completed_event_strategy())
    def test_event_detail_rule_results_has_exactly_six_elements(
        self, event: ComplianceCompletedEvent
    ) -> None:
        """rule_results SHALL conter exatamente 6 elementos.

        Cada elemento deve ter rule_id (string), status (string)
        e confidence (int).
        """
        with patch("brand_watchdog.events.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.put_events.return_value = _create_success_response()

            publisher = EventPublisher()
            result = asyncio.run(
                publisher.publish_compliance_completed(event)
            )

            assert result is True

            call_kwargs = mock_client.put_events.call_args[1]
            detail_json = call_kwargs["Entries"][0]["Detail"]
            detail = json.loads(detail_json)

            rule_results = detail["rule_results"]

            # Verificar que tem exatamente 6 elementos
            assert len(rule_results) == 6, (
                f"rule_results deveria ter 6 elementos, "
                f"obtido: {len(rule_results)}"
            )

            # Verificar estrutura de cada resultado
            for i, rr in enumerate(rule_results):
                assert "rule_id" in rr, (
                    f"rule_results[{i}] não contém 'rule_id'"
                )
                assert "status" in rr, (
                    f"rule_results[{i}] não contém 'status'"
                )
                assert "confidence" in rr, (
                    f"rule_results[{i}] não contém 'confidence'"
                )
                assert isinstance(rr["rule_id"], str), (
                    f"rule_results[{i}].rule_id deveria ser string, "
                    f"obtido: {type(rr['rule_id'])}"
                )
                assert isinstance(rr["status"], str), (
                    f"rule_results[{i}].status deveria ser string, "
                    f"obtido: {type(rr['status'])}"
                )
                assert isinstance(rr["confidence"], int), (
                    f"rule_results[{i}].confidence deveria ser int, "
                    f"obtido: {type(rr['confidence'])}"
                )

    @_PBT_SETTINGS
    @given(event=compliance_completed_event_strategy())
    def test_event_detail_field_types_are_correct(
        self, event: ComplianceCompletedEvent
    ) -> None:
        """Os tipos dos campos no 'detail' SHALL estar corretos.

        - site_id: string (formato UUID)
        - cycle_id: string (formato UUID)
        - target_url: string
        - brand: "sky_plus" ou "dgo"
        - overall_status: "compliant" ou "non_compliant"
        - rule_results: lista
        - screenshot_s3_key: string
        - analyzed_at: string (ISO 8601 UTC)
        """
        with patch("brand_watchdog.events.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.put_events.return_value = _create_success_response()

            publisher = EventPublisher()
            result = asyncio.run(
                publisher.publish_compliance_completed(event)
            )

            assert result is True

            call_kwargs = mock_client.put_events.call_args[1]
            detail_json = call_kwargs["Entries"][0]["Detail"]
            detail = json.loads(detail_json)

            # site_id: UUID string
            assert isinstance(detail["site_id"], str)
            assert _UUID_REGEX.match(detail["site_id"]), (
                f"site_id não é UUID válido: {detail['site_id']}"
            )

            # cycle_id: UUID string
            assert isinstance(detail["cycle_id"], str)
            assert _UUID_REGEX.match(detail["cycle_id"]), (
                f"cycle_id não é UUID válido: {detail['cycle_id']}"
            )

            # target_url: string
            assert isinstance(detail["target_url"], str)
            assert len(detail["target_url"]) > 0, "target_url não pode ser vazio"

            # brand: valores válidos
            assert detail["brand"] in ("sky_plus", "dgo"), (
                f"brand inválido: {detail['brand']}"
            )

            # overall_status: valores válidos
            assert detail["overall_status"] in (
                "compliant",
                "non_compliant",
            ), (
                f"overall_status inválido: {detail['overall_status']}"
            )

            # rule_results: lista
            assert isinstance(detail["rule_results"], list), (
                f"rule_results deveria ser list, obtido: "
                f"{type(detail['rule_results'])}"
            )

            # screenshot_s3_key: string
            assert isinstance(detail["screenshot_s3_key"], str)
            assert len(detail["screenshot_s3_key"]) > 0, (
                "screenshot_s3_key não pode ser vazio"
            )

            # analyzed_at: ISO 8601 UTC
            assert isinstance(detail["analyzed_at"], str)
            assert _ISO8601_UTC_REGEX.match(detail["analyzed_at"]), (
                f"analyzed_at não está em ISO 8601 UTC: "
                f"{detail['analyzed_at']}"
            )
