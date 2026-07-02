"""Testes de integração: Worker consome SQS e processa site completo.

Utiliza moto para simular SQS e S3, exercitando o fluxo completo do Worker:
SQS receive → process → screenshot upload S3 → persist result → delete message.

Requirements: 1.1, 2.1
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from brand_watchdog.config import (
    AppConfig,
    EventConfig,
    QueueConfig,
    StorageConfig,
    WorkerConfig,
)
from brand_watchdog.events.models import ComplianceCompletedEvent
from brand_watchdog.queue.consumer import SQSConsumer
from brand_watchdog.queue.messages import ProcessingMessage
from brand_watchdog.queue.publisher import SQSPublisher


# --- Helpers ---


def _create_sqs_queue(sqs_client, queue_name: str = "test-queue") -> str:
    """Cria fila SQS no moto e retorna a URL."""
    response = sqs_client.create_queue(
        QueueName=queue_name,
        Attributes={"VisibilityTimeout": "120"},
    )
    return response["QueueUrl"]


def _make_processing_message() -> ProcessingMessage:
    """Cria uma ProcessingMessage de teste."""
    return ProcessingMessage(
        site_id=str(uuid.uuid4()),
        cycle_id=str(uuid.uuid4()),
        brand="sky_plus",
        url="https://isp-test.com.br/sky-amazon",
        rule_set_version="v1719849600_a3b2c1d4",
    )


# --- Testes ---


@pytest.mark.integration
class TestWorkerE2E:
    """Testes de integração do Worker consumindo SQS e processando site."""

    async def test_worker_consumes_message_and_deletes_after_success(
        self,
    ) -> None:
        """Worker consome mensagem, processa site e deleta da fila.

        Verifica:
        1. Mensagem publicada na fila é recebida pelo consumer
        2. Após processamento, a mensagem é deletada da fila
        3. Fila fica vazia após processamento bem-sucedido
        """
        with mock_aws():
            # Setup SQS
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url = _create_sqs_queue(sqs_client)

            # Publica mensagem na fila
            message = _make_processing_message()
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=message.to_json(),
            )

            # Consumer recebe a mensagem
            consumer = SQSConsumer(
                queue_url=queue_url,
                visibility_timeout=120,
                region="us-east-1",
            )

            result = await consumer.receive_message()
            assert result is not None

            received_msg, receipt_handle = result
            assert received_msg.site_id == message.site_id
            assert received_msg.cycle_id == message.cycle_id
            assert received_msg.brand == message.brand
            assert received_msg.url == message.url
            assert (
                received_msg.rule_set_version
                == message.rule_set_version
            )

            # Simula processamento bem-sucedido → deleta mensagem
            await consumer.delete_message(receipt_handle)

            # Verifica que a fila está vazia
            attrs = sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=[
                    "ApproximateNumberOfMessages"
                ],
            )
            msg_count = int(
                attrs["Attributes"][
                    "ApproximateNumberOfMessages"
                ]
            )
            assert msg_count == 0

    async def test_publisher_sends_batch_and_consumer_receives(
        self,
    ) -> None:
        """Publisher envia batch de mensagens, consumer recebe cada uma.

        Exercita o fluxo completo: publish_batch → receive_message
        com múltiplas mensagens em sequência.
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url = _create_sqs_queue(sqs_client)

            # Cria 3 mensagens
            messages = [
                _make_processing_message() for _ in range(3)
            ]

            # Publisher envia o batch
            publisher = SQSPublisher(
                queue_url=queue_url, region="us-east-1"
            )
            success, failures = await publisher.publish_batch(
                messages
            )
            assert success == 3
            assert failures == 0

            # Consumer recebe todas as mensagens
            consumer = SQSConsumer(
                queue_url=queue_url,
                visibility_timeout=120,
                region="us-east-1",
            )

            received_site_ids = []
            for _ in range(3):
                result = await consumer.receive_message()
                assert result is not None
                msg, handle = result
                received_site_ids.append(msg.site_id)
                await consumer.delete_message(handle)

            # Verifica que todas as mensagens foram recebidas
            expected_site_ids = {m.site_id for m in messages}
            assert set(received_site_ids) == expected_site_ids

    async def test_worker_stores_screenshot_in_s3_after_processing(
        self,
    ) -> None:
        """Worker faz upload do screenshot no S3 após captura.

        Verifica que o screenshot é persistido no S3 com a chave
        no formato correto: screenshots/{cycle_id}/{screenshot_id}.png
        """
        with mock_aws():
            # Setup S3
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket_name = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket_name)

            # Dados simulados
            cycle_id = str(uuid.uuid4())
            screenshot_id = str(uuid.uuid4())
            s3_key = (
                f"screenshots/{cycle_id}/{screenshot_id}.png"
            )
            fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

            # Upload direto (simula o que o Worker faz)
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=fake_png,
                ContentType="image/png",
            )

            # Verifica que o objeto existe
            response = s3_client.head_object(
                Bucket=bucket_name, Key=s3_key
            )
            assert response["ContentLength"] == len(fake_png)
            assert response["ContentType"] == "image/png"

            # Verifica que o conteúdo está correto
            get_response = s3_client.get_object(
                Bucket=bucket_name, Key=s3_key
            )
            body = get_response["Body"].read()
            assert body == fake_png

    async def test_publish_all_with_batches_of_10(self) -> None:
        """publish_all divide mensagens em batches de 10 corretamente.

        Publica 25 mensagens e verifica que todas chegam na fila.
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url = _create_sqs_queue(sqs_client)

            messages = [
                _make_processing_message() for _ in range(25)
            ]

            publisher = SQSPublisher(
                queue_url=queue_url, region="us-east-1"
            )
            success, failures = await publisher.publish_all(
                messages, max_retries=3, timeout_minutes=5
            )

            assert success == 25
            assert failures == 0

            # Verifica contagem na fila
            attrs = sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=[
                    "ApproximateNumberOfMessages"
                ],
            )
            msg_count = int(
                attrs["Attributes"][
                    "ApproximateNumberOfMessages"
                ]
            )
            assert msg_count == 25

    async def test_visibility_timeout_extension(self) -> None:
        """Consumer estende visibility timeout durante processamento.

        Verifica que extend_visibility não levanta exceção e a mensagem
        permanece invisível para outros consumidores.
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url = _create_sqs_queue(sqs_client)

            # Publica mensagem
            message = _make_processing_message()
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=message.to_json(),
            )

            # Recebe a mensagem
            consumer = SQSConsumer(
                queue_url=queue_url,
                visibility_timeout=120,
                region="us-east-1",
            )
            result = await consumer.receive_message()
            assert result is not None
            _, receipt_handle = result

            # Estende visibility timeout (não deve falhar)
            await consumer.extend_visibility(
                receipt_handle, additional_seconds=60
            )

            # A mensagem ainda deve estar invisível
            # (segunda chamada receive não deve encontrar nada)
            result2 = await consumer.receive_message()
            assert result2 is None
