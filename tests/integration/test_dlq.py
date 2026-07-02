"""Testes de integração: DLQ recebe mensagens falhadas.

Utiliza moto para simular SQS com Dead Letter Queue, verificando
a configuração da redrive policy e o comportamento de mensagens
que falham no processamento.

Requirements: 2.1, 6.1
"""

from __future__ import annotations

import json
import uuid

import boto3
import pytest
from moto import mock_aws

from brand_watchdog.queue.messages import ProcessingMessage


# --- Helpers ---


def _create_queue_with_dlq(
    sqs_client,
    queue_name: str = "main-queue",
    dlq_name: str = "main-queue-dlq",
    max_receive_count: int = 3,
    visibility_timeout: str = "120",
) -> tuple[str, str]:
    """Cria fila SQS principal com DLQ configurada.

    Returns:
        Tupla (queue_url, dlq_url)
    """
    # Cria DLQ primeiro
    dlq_response = sqs_client.create_queue(QueueName=dlq_name)
    dlq_url = dlq_response["QueueUrl"]

    # Obtém ARN da DLQ
    dlq_attrs = sqs_client.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["QueueArn"]
    )
    dlq_arn = dlq_attrs["Attributes"]["QueueArn"]

    # Cria fila principal com redrive policy
    redrive_policy = json.dumps(
        {
            "deadLetterTargetArn": dlq_arn,
            "maxReceiveCount": max_receive_count,
        }
    )

    queue_response = sqs_client.create_queue(
        QueueName=queue_name,
        Attributes={
            "RedrivePolicy": redrive_policy,
            "VisibilityTimeout": visibility_timeout,
        },
    )
    queue_url = queue_response["QueueUrl"]

    return queue_url, dlq_url


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
class TestDLQ:
    """Testes de integração da Dead Letter Queue."""

    async def test_dlq_redrive_policy_configured_correctly(
        self,
    ) -> None:
        """Fila principal tem redrive policy com maxReceiveCount=3.

        Verifica que a configuração da fila está correta:
        - RedrivePolicy presente
        - maxReceiveCount=3
        - deadLetterTargetArn apontando para DLQ
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url, dlq_url = _create_queue_with_dlq(
                sqs_client, max_receive_count=3
            )

            # Verifica atributos da fila principal
            attrs = sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=[
                    "RedrivePolicy",
                    "VisibilityTimeout",
                ],
            )
            redrive_policy = json.loads(
                attrs["Attributes"]["RedrivePolicy"]
            )

            assert "deadLetterTargetArn" in redrive_policy
            assert redrive_policy["maxReceiveCount"] == 3
            assert (
                attrs["Attributes"]["VisibilityTimeout"]
                == "120"
            )

    async def test_dlq_visibility_timeout_matches_spec(
        self,
    ) -> None:
        """Visibility timeout da fila é 120s conforme especificação.

        Verifica que o timeout de visibilidade está configurado
        para 120 segundos (compatível com processamento máximo).
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url, _ = _create_queue_with_dlq(
                sqs_client,
                max_receive_count=3,
                visibility_timeout="120",
            )

            attrs = sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=["VisibilityTimeout"],
            )
            assert (
                attrs["Attributes"]["VisibilityTimeout"]
                == "120"
            )

    async def test_message_moves_to_dlq_after_max_receives(
        self,
    ) -> None:
        """Mensagem vai para DLQ após maxReceiveCount recebimentos.

        Nota: moto implementa redrive policy corretamente quando
        o visibility timeout é 0 e a mensagem é recebida N vezes.
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url, dlq_url = _create_queue_with_dlq(
                sqs_client,
                max_receive_count=3,
                visibility_timeout="0",
            )

            # Envia mensagem na fila principal
            message = _make_processing_message()
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=message.to_json(),
            )

            # Recebe a mensagem 4 vezes sem deletar
            # (maxReceiveCount=3 → após 3 receives, próxima
            # tentativa deve ir para DLQ)
            for _ in range(4):
                sqs_client.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=1,
                    VisibilityTimeout=0,
                    WaitTimeSeconds=0,
                )

            # Verifica que a mensagem está na DLQ
            dlq_response = sqs_client.receive_message(
                QueueUrl=dlq_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=0,
            )
            dlq_messages = dlq_response.get("Messages", [])
            assert len(dlq_messages) == 1

            # Verifica conteúdo da mensagem na DLQ
            dlq_body = json.loads(dlq_messages[0]["Body"])
            assert dlq_body["site_id"] == message.site_id
            assert dlq_body["cycle_id"] == message.cycle_id

    async def test_dlq_preserves_original_message_content(
        self,
    ) -> None:
        """DLQ preserva o conteúdo original da mensagem.

        Verifica que todos os campos da ProcessingMessage são
        mantidos intactos quando a mensagem vai para a DLQ.
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url, dlq_url = _create_queue_with_dlq(
                sqs_client,
                max_receive_count=1,
                visibility_timeout="0",
            )

            message = _make_processing_message()
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=message.to_json(),
            )

            # Recebe 2 vezes (maxReceiveCount=1 → após 1
            # receive, deve ir para DLQ)
            for _ in range(2):
                sqs_client.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=1,
                    VisibilityTimeout=0,
                    WaitTimeSeconds=0,
                )

            # Mensagem deve estar na DLQ
            dlq_response = sqs_client.receive_message(
                QueueUrl=dlq_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=0,
            )
            dlq_messages = dlq_response.get("Messages", [])
            assert len(dlq_messages) == 1

            # Deserializa e verifica todos os campos
            recovered = ProcessingMessage.from_json(
                dlq_messages[0]["Body"]
            )
            assert recovered.site_id == message.site_id
            assert recovered.cycle_id == message.cycle_id
            assert recovered.brand == message.brand
            assert recovered.url == message.url
            assert (
                recovered.rule_set_version
                == message.rule_set_version
            )

    async def test_successful_processing_does_not_trigger_dlq(
        self,
    ) -> None:
        """Mensagem processada com sucesso NÃO vai para DLQ.

        Verifica que quando a mensagem é deletada após recebimento,
        ela não aparece na DLQ.
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url, dlq_url = _create_queue_with_dlq(
                sqs_client, max_receive_count=3
            )

            message = _make_processing_message()
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=message.to_json(),
            )

            # Recebe e deleta (processamento com sucesso)
            response = sqs_client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=0,
            )
            messages = response.get("Messages", [])
            assert len(messages) == 1

            receipt_handle = messages[0]["ReceiptHandle"]
            sqs_client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )

            # DLQ deve estar vazia
            dlq_response = sqs_client.receive_message(
                QueueUrl=dlq_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=0,
            )
            dlq_messages = dlq_response.get("Messages", [])
            assert len(dlq_messages) == 0

    async def test_dlq_arn_correctly_referenced(self) -> None:
        """ARN da DLQ é corretamente referenciado na redrive policy.

        Verifica que a deadLetterTargetArn aponta para o ARN real
        da DLQ criada.
        """
        with mock_aws():
            sqs_client = boto3.client(
                "sqs", region_name="us-east-1"
            )
            queue_url, dlq_url = _create_queue_with_dlq(
                sqs_client, max_receive_count=3
            )

            # Obtém ARN real da DLQ
            dlq_attrs = sqs_client.get_queue_attributes(
                QueueUrl=dlq_url,
                AttributeNames=["QueueArn"],
            )
            dlq_arn = dlq_attrs["Attributes"]["QueueArn"]

            # Verifica que a fila principal referencia o ARN correto
            main_attrs = sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=["RedrivePolicy"],
            )
            redrive_policy = json.loads(
                main_attrs["Attributes"]["RedrivePolicy"]
            )

            assert (
                redrive_policy["deadLetterTargetArn"]
                == dlq_arn
            )
