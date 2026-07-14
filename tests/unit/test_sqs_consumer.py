"""Testes unitários para SQSConsumer.

Valida:
- receive_message: recebimento com long polling, MaxNumberOfMessages=1
- delete_message: deleção por receipt_handle após sucesso
- extend_visibility: renovação de visibility timeout
- Tratamento de erros: falhas de API e deserialização

Requirements: 2.1, 2.2, 2.3, 2.6, 2.7
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from brand_watchdog.queue.consumer import SQSConsumer
from brand_watchdog.queue.messages import ProcessingMessage

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456/test-queue"


def _make_sqs_response(
    body: str = "",
    receipt_handle: str = "test-receipt-handle-123",
) -> dict:
    """Cria resposta simulada do ReceiveMessage."""
    if not body:
        msg = ProcessingMessage(
            site_id="site-001",
            cycle_id="cycle-001",
            brand="sky_plus",
            url="https://example.com/partner",
            rule_set_version="v1719849600_a3b2c1d4",
        )
        body = msg.to_json()
    return {
        "Messages": [
            {
                "Body": body,
                "ReceiptHandle": receipt_handle,
                "MessageId": "msg-id-001",
            }
        ]
    }


def _empty_response() -> dict:
    """Cria resposta de fila vazia."""
    return {"Messages": []}


class TestReceiveMessage:
    """Testes para receive_message."""

    @pytest.mark.asyncio
    async def test_returns_message_and_receipt_handle(self):
        """Retorna tupla (ProcessingMessage, receipt_handle)."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.receive_message.return_value = (
                _make_sqs_response(
                    receipt_handle="handle-abc"
                )
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            result = await consumer.receive_message()

            assert result is not None
            message, receipt_handle = result
            assert isinstance(message, ProcessingMessage)
            assert message.site_id == "site-001"
            assert message.cycle_id == "cycle-001"
            assert message.brand == "sky_plus"
            assert message.url == "https://example.com/partner"
            assert message.rule_set_version == "v1719849600_a3b2c1d4"
            assert receipt_handle == "handle-abc"

    @pytest.mark.asyncio
    async def test_returns_none_when_queue_empty(self):
        """Retorna None quando a fila está vazia."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.receive_message.return_value = (
                _empty_response()
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            result = await consumer.receive_message()

            assert result is None

    @pytest.mark.asyncio
    async def test_uses_correct_parameters(self):
        """Chama ReceiveMessage com parâmetros corretos."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.receive_message.return_value = (
                _empty_response()
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(
                queue_url=QUEUE_URL,
                visibility_timeout=120,
            )
            await consumer.receive_message()

            mock_client.receive_message.assert_called_once_with(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                VisibilityTimeout=120,
                WaitTimeSeconds=20,
            )

    @pytest.mark.asyncio
    async def test_custom_visibility_timeout(self):
        """Respeita visibility_timeout personalizado."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.receive_message.return_value = (
                _empty_response()
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(
                queue_url=QUEUE_URL,
                visibility_timeout=180,
            )
            await consumer.receive_message()

            call_kwargs = (
                mock_client.receive_message.call_args.kwargs
            )
            assert call_kwargs["VisibilityTimeout"] == 180

    @pytest.mark.asyncio
    async def test_returns_none_on_client_error(self):
        """Retorna None quando ocorre erro de API."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.receive_message.side_effect = ClientError(
                {"Error": {"Code": "AWS.SimpleQueueService."
                           "NonExistentQueue",
                           "Message": "Queue not found"}},
                "ReceiveMessage",
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            result = await consumer.receive_message()

            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        """Retorna None quando o corpo da mensagem é JSON inválido."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.receive_message.return_value = (
                _make_sqs_response(body="invalid json {{{")
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            result = await consumer.receive_message()

            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_missing_fields(self):
        """Retorna None quando campos obrigatórios estão ausentes."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.receive_message.return_value = (
                _make_sqs_response(body='{"site_id": "x"}')
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            result = await consumer.receive_message()

            assert result is None


class TestDeleteMessage:
    """Testes para delete_message."""

    @pytest.mark.asyncio
    async def test_deletes_with_receipt_handle(self):
        """Deleta mensagem usando receipt_handle."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.delete_message.return_value = {}
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            await consumer.delete_message("receipt-handle-xyz")

            mock_client.delete_message.assert_called_once_with(
                QueueUrl=QUEUE_URL,
                ReceiptHandle="receipt-handle-xyz",
            )

    @pytest.mark.asyncio
    async def test_raises_on_client_error(self):
        """Lança exceção quando deleção falha."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.delete_message.side_effect = ClientError(
                {"Error": {"Code": "ReceiptHandleIsInvalid",
                           "Message": "Invalid handle"}},
                "DeleteMessage",
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)

            with pytest.raises(ClientError):
                await consumer.delete_message("invalid-handle")


class TestExtendVisibility:
    """Testes para extend_visibility."""

    @pytest.mark.asyncio
    async def test_extends_with_default_seconds(self):
        """Estende visibility com 60s (padrão)."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.change_message_visibility.return_value = {}
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            await consumer.extend_visibility("receipt-handle-abc")

            mock_client.change_message_visibility.assert_called_once_with(
                QueueUrl=QUEUE_URL,
                ReceiptHandle="receipt-handle-abc",
                VisibilityTimeout=60,
            )

    @pytest.mark.asyncio
    async def test_extends_with_custom_seconds(self):
        """Estende visibility com valor personalizado."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.change_message_visibility.return_value = {}
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)
            await consumer.extend_visibility(
                "receipt-handle-abc",
                additional_seconds=90,
            )

            call_kwargs = (
                mock_client
                .change_message_visibility
                .call_args.kwargs
            )
            assert call_kwargs["VisibilityTimeout"] == 90

    @pytest.mark.asyncio
    async def test_raises_on_client_error(self):
        """Lança exceção quando extensão falha."""
        with patch("brand_watchdog.queue.consumer.boto3") as mb:
            mock_client = MagicMock()
            mock_client.change_message_visibility.side_effect = (
                ClientError(
                    {"Error": {"Code": "MessageNotInflight",
                               "Message": "Not in flight"}},
                    "ChangeMessageVisibility",
                )
            )
            mb.client.return_value = mock_client

            consumer = SQSConsumer(queue_url=QUEUE_URL)

            with pytest.raises(ClientError):
                await consumer.extend_visibility("bad-handle")
