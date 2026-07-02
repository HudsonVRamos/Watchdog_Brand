"""Property test para completude da publicação em batch do SQSPublisher.

# Feature: architecture-evolution, Property 1: Completude da Publicação em Batch

**Validates: Requirements 1.1**

Para qualquer lista de Target Sites ativos (de 1 a 200 sites), o
`SQSPublisher.publish_all` SHALL produzir exatamente `ceil(N/10)` chamadas
`SendMessageBatch` e cada site SHALL ter exatamente uma mensagem publicada
(ou registrada como falha após retries).
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from brand_watchdog.queue.messages import ProcessingMessage
from brand_watchdog.queue.publisher import SQSPublisher


_PBT_SETTINGS = settings(max_examples=30, deadline=None)

# -- Estratégias de geração de dados --

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
def processing_message_strategy(draw: st.DrawFn) -> ProcessingMessage:
    """Gera uma ProcessingMessage válida."""
    return ProcessingMessage(
        site_id=draw(_uuid_strategy),
        cycle_id=draw(_uuid_strategy),
        brand=draw(_brand_strategy),
        url=draw(_url_strategy),
        rule_set_version=draw(_rule_set_version_strategy),
    )


@st.composite
def message_list_strategy(draw: st.DrawFn) -> list[ProcessingMessage]:
    """Gera uma lista de 1 a 200 ProcessingMessages."""
    return draw(
        st.lists(
            processing_message_strategy(),
            min_size=1,
            max_size=200,
        )
    )


def _create_success_response(entries: list[dict]) -> dict:
    """Cria resposta SQS simulando sucesso total para um batch."""
    return {
        "Successful": [{"Id": e["Id"]} for e in entries],
        "Failed": [],
    }


class TestSQSPublisherBatchCompleteness:
    """Property 1: Completude da Publicação em Batch.

    **Validates: Requirements 1.1**
    """

    @_PBT_SETTINGS
    @given(messages=message_list_strategy())
    def test_publish_all_produces_correct_number_of_batch_calls(
        self, messages: list[ProcessingMessage]
    ) -> None:
        """publish_all SHALL produzir exatamente ceil(N/10) chamadas SendMessageBatch.

        Com SQS sempre retornando sucesso, o número de chamadas deve ser
        exatamente ceil(N/10) — sem retries necessários.
        """
        n = len(messages)
        expected_batches = math.ceil(n / 10)

        with patch("brand_watchdog.queue.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.send_message_batch.side_effect = (
                lambda **kwargs: _create_success_response(kwargs["Entries"])
            )

            publisher = SQSPublisher(
                queue_url="https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
                region="us-east-1",
            )

            import asyncio
            success, failures = asyncio.run(
                publisher.publish_all(messages, max_retries=3)
            )

            actual_batches = mock_client.send_message_batch.call_count
            assert actual_batches == expected_batches, (
                f"Esperado {expected_batches} chamadas SendMessageBatch para "
                f"{n} mensagens, mas foram feitas {actual_batches} chamadas."
            )

    @_PBT_SETTINGS
    @given(messages=message_list_strategy())
    def test_publish_all_accounts_for_all_messages(
        self, messages: list[ProcessingMessage]
    ) -> None:
        """Todas as mensagens SHALL ser contabilizadas (sucesso + falha = N).

        Com SQS retornando sucesso, total_sucesso + total_falhas deve ser
        igual ao número total de mensagens enviadas.
        """
        n = len(messages)

        with patch("brand_watchdog.queue.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.send_message_batch.side_effect = (
                lambda **kwargs: _create_success_response(kwargs["Entries"])
            )

            publisher = SQSPublisher(
                queue_url="https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
                region="us-east-1",
            )

            import asyncio
            success, failures = asyncio.run(
                publisher.publish_all(messages, max_retries=3)
            )

            total = success + failures
            assert total == n, (
                f"Total (sucesso={success} + falhas={failures} = {total}) "
                f"diverge do número de mensagens enviadas ({n})."
            )
            # Com mock de sucesso, todas devem ter sucesso
            assert success == n, (
                f"Esperado {n} sucessos com mock de sucesso total, "
                f"mas obtido {success}."
            )
            assert failures == 0, (
                f"Esperado 0 falhas com mock de sucesso total, "
                f"mas obtido {failures}."
            )

    @_PBT_SETTINGS
    @given(messages=message_list_strategy())
    def test_publish_all_with_partial_failures_accounts_all(
        self, messages: list[ProcessingMessage]
    ) -> None:
        """Com falhas parciais + retry, total (sucesso + falha) SHALL ser N.

        Simula o SQS retornando falha para a última mensagem de cada batch
        na primeira tentativa, e sucesso na segunda tentativa (retry).
        O total contabilizado deve ser sempre N.
        """
        n = len(messages)
        call_counter = {"count": 0}

        def mixed_response(**kwargs):
            """Primeira chamada de cada batch falha na última mensagem.
            Retry subsequente retorna sucesso total."""
            entries = kwargs["Entries"]
            call_counter["count"] += 1

            # Para simular retry: na primeira chamada de um batch com >1 msg,
            # a última mensagem falha. Na chamada de retry (batch com 1 msg),
            # sempre sucesso.
            if len(entries) > 1:
                return {
                    "Successful": [{"Id": e["Id"]} for e in entries[:-1]],
                    "Failed": [
                        {
                            "Id": entries[-1]["Id"],
                            "Code": "InternalError",
                            "Message": "Simulated failure",
                            "SenderFault": False,
                        }
                    ],
                }
            else:
                return _create_success_response(entries)

        with patch("brand_watchdog.queue.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.send_message_batch.side_effect = mixed_response

            publisher = SQSPublisher(
                queue_url="https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
                region="us-east-1",
            )

            import asyncio
            success, failures = asyncio.run(
                publisher.publish_all(messages, max_retries=3)
            )

            total = success + failures
            assert total == n, (
                f"Com falhas parciais + retry, total "
                f"(sucesso={success} + falhas={failures} = {total}) "
                f"diverge do número de mensagens enviadas ({n})."
            )

    @_PBT_SETTINGS
    @given(messages=message_list_strategy())
    def test_each_message_published_exactly_once_on_success(
        self, messages: list[ProcessingMessage]
    ) -> None:
        """Cada site SHALL ter exatamente uma mensagem publicada com sucesso.

        Verifica que os corpos de mensagem enviados ao SQS correspondem
        exatamente às mensagens originais (sem duplicatas ou omissões).
        """
        n = len(messages)
        published_bodies: list[str] = []

        def capture_response(**kwargs):
            entries = kwargs["Entries"]
            for entry in entries:
                published_bodies.append(entry["MessageBody"])
            return _create_success_response(entries)

        with patch("brand_watchdog.queue.publisher.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.send_message_batch.side_effect = capture_response

            publisher = SQSPublisher(
                queue_url="https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
                region="us-east-1",
            )

            import asyncio
            asyncio.run(publisher.publish_all(messages, max_retries=3))

            # Verificar que cada mensagem original foi publicada
            expected_bodies = [msg.to_json() for msg in messages]
            assert len(published_bodies) == n, (
                f"Esperado {n} mensagens publicadas, "
                f"mas foram publicadas {len(published_bodies)}."
            )
            assert sorted(published_bodies) == sorted(expected_bodies), (
                f"Corpos das mensagens publicadas não correspondem "
                f"às mensagens originais."
            )
