"""Property test para completude da serialização de ProcessingMessage.

# Feature: architecture-evolution, Property 2: Completude da Serialização de Mensagem

**Validates: Requirements 1.2**

Para qualquer ProcessingMessage válida com site_id (UUID), cycle_id (UUID),
brand ("sky_plus" ou "dgo"), url (string ≤ 2048 chars) e rule_set_version
(formato "v{timestamp}_{hash_8}"), a serialização para JSON SHALL conter
todos os 5 campos obrigatórios com tipos corretos.
"""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from brand_watchdog.queue.messages import ProcessingMessage


_PBT_SETTINGS = settings(max_examples=30)

# -- Estratégias de geração de dados válidos --

_uuid_strategy = st.uuids().map(str)

_brand_strategy = st.sampled_from(["sky_plus", "dgo"])

_url_strategy = st.from_regex(
    r"https://[a-z]{3,20}\.[a-z]{2,5}(/[a-z0-9_\-]{1,50}){0,5}",
    fullmatch=True,
).filter(lambda u: len(u) <= 2048)

_rule_set_version_strategy = st.builds(
    lambda ts, h: f"v{ts}_{h}",
    ts=st.integers(min_value=1_000_000_000, max_value=9_999_999_999),
    h=st.from_regex(r"[0-9a-f]{8}", fullmatch=True),
)


@st.composite
def processing_message(draw: st.DrawFn) -> ProcessingMessage:
    """Gera uma ProcessingMessage válida conforme os requisitos."""
    return ProcessingMessage(
        site_id=draw(_uuid_strategy),
        cycle_id=draw(_uuid_strategy),
        brand=draw(_brand_strategy),
        url=draw(_url_strategy),
        rule_set_version=draw(_rule_set_version_strategy),
    )


class TestProcessingMessageSerializationCompleteness:
    """Property 2: Completude da Serialização de Mensagem.

    **Validates: Requirements 1.2**
    """

    @_PBT_SETTINGS
    @given(msg=processing_message())
    def test_serialization_contains_all_required_fields(
        self, msg: ProcessingMessage
    ) -> None:
        """A serialização JSON SHALL conter todos os 5 campos obrigatórios."""
        json_str = msg.to_json()
        data = json.loads(json_str)

        required_fields = {"site_id", "cycle_id", "brand", "url", "rule_set_version"}
        present_fields = set(data.keys())

        missing = required_fields - present_fields
        assert not missing, (
            f"Campos obrigatórios ausentes na serialização: {sorted(missing)}. "
            f"Mensagem original: {msg}"
        )

    @_PBT_SETTINGS
    @given(msg=processing_message())
    def test_serialized_fields_have_correct_types(
        self, msg: ProcessingMessage
    ) -> None:
        """Todos os campos serializados SHALL ter tipo string."""
        json_str = msg.to_json()
        data = json.loads(json_str)

        for field in ("site_id", "cycle_id", "brand", "url", "rule_set_version"):
            assert isinstance(data[field], str), (
                f"Campo '{field}' deveria ser string, mas é {type(data[field]).__name__}. "
                f"Valor: {data[field]!r}"
            )

    @_PBT_SETTINGS
    @given(msg=processing_message())
    def test_serialized_values_match_original(
        self, msg: ProcessingMessage
    ) -> None:
        """Os valores serializados SHALL corresponder aos valores originais."""
        json_str = msg.to_json()
        data = json.loads(json_str)

        assert data["site_id"] == msg.site_id, (
            f"site_id diverge: {data['site_id']!r} != {msg.site_id!r}"
        )
        assert data["cycle_id"] == msg.cycle_id, (
            f"cycle_id diverge: {data['cycle_id']!r} != {msg.cycle_id!r}"
        )
        assert data["brand"] == msg.brand, (
            f"brand diverge: {data['brand']!r} != {msg.brand!r}"
        )
        assert data["url"] == msg.url, (
            f"url diverge: {data['url']!r} != {msg.url!r}"
        )
        assert data["rule_set_version"] == msg.rule_set_version, (
            f"rule_set_version diverge: {data['rule_set_version']!r} "
            f"!= {msg.rule_set_version!r}"
        )

    @_PBT_SETTINGS
    @given(msg=processing_message())
    def test_serialization_produces_valid_json(
        self, msg: ProcessingMessage
    ) -> None:
        """A serialização SHALL produzir JSON válido parseable."""
        json_str = msg.to_json()

        # Deve ser uma string não-vazia
        assert isinstance(json_str, str), (
            f"to_json() deveria retornar str, mas retornou {type(json_str).__name__}"
        )
        assert len(json_str) > 0, "to_json() retornou string vazia"

        # Deve ser JSON válido (não deve lançar exceção)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict), (
            f"JSON parseado deveria ser dict, mas é {type(parsed).__name__}"
        )

    @_PBT_SETTINGS
    @given(msg=processing_message())
    def test_round_trip_preserves_data(
        self, msg: ProcessingMessage
    ) -> None:
        """Serializar e deserializar SHALL preservar todos os dados."""
        json_str = msg.to_json()
        restored = ProcessingMessage.from_json(json_str)

        assert restored.site_id == msg.site_id, (
            f"Round-trip site_id diverge: {restored.site_id!r} != {msg.site_id!r}"
        )
        assert restored.cycle_id == msg.cycle_id, (
            f"Round-trip cycle_id diverge: {restored.cycle_id!r} != {msg.cycle_id!r}"
        )
        assert restored.brand == msg.brand, (
            f"Round-trip brand diverge: {restored.brand!r} != {msg.brand!r}"
        )
        assert restored.url == msg.url, (
            f"Round-trip url diverge: {restored.url!r} != {msg.url!r}"
        )
        assert restored.rule_set_version == msg.rule_set_version, (
            f"Round-trip rule_set_version diverge: {restored.rule_set_version!r} "
            f"!= {msg.rule_set_version!r}"
        )
