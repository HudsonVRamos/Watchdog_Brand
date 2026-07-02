"""Consumer de mensagens da fila SQS.

Responsável por consumir mensagens de processamento da fila SQS,
gerenciar visibility timeout e deletar mensagens após processamento
bem-sucedido.
"""

from __future__ import annotations

import asyncio
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from brand_watchdog.queue.messages import ProcessingMessage

logger = logging.getLogger(__name__)


class SQSConsumer:
    """Consome mensagens de processamento da fila SQS.

    Recebe uma mensagem por vez, gerencia visibility timeout
    e deleta mensagens após processamento bem-sucedido.

    Args:
        queue_url: URL da fila SQS de origem.
        visibility_timeout: Tempo em segundos que a mensagem fica
            invisível após recebimento. Padrão: 120.
        region: Região AWS da fila. Padrão: "us-east-1".
    """

    def __init__(
        self,
        queue_url: str,
        visibility_timeout: int = 120,
        region: str = "us-east-1",
    ) -> None:
        self._queue_url = queue_url
        self._visibility_timeout = visibility_timeout
        self._region = region
        self._client = boto3.client("sqs", region_name=region)

    async def receive_message(
        self,
    ) -> tuple[ProcessingMessage, str] | None:
        """Recebe uma mensagem da fila SQS.

        Utiliza long polling (WaitTimeSeconds=20) para reduzir
        chamadas desnecessárias. Recebe no máximo 1 mensagem
        por chamada.

        Returns:
            Tupla (ProcessingMessage, receipt_handle) se houver
            mensagem disponível, ou None se a fila estiver vazia.
        """
        try:
            response = await asyncio.to_thread(
                self._client.receive_message,
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=1,
                VisibilityTimeout=self._visibility_timeout,
                WaitTimeSeconds=20,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Falha ao receber mensagem da fila SQS: "
                "queue_url=%s, erro=%s",
                self._queue_url,
                str(exc),
            )
            return None

        messages = response.get("Messages", [])
        if not messages:
            return None

        sqs_message = messages[0]
        body = sqs_message.get("Body", "")
        receipt_handle = sqs_message.get("ReceiptHandle", "")

        try:
            processing_message = ProcessingMessage.from_json(body)
        except (ValueError, Exception) as exc:
            logger.error(
                "Falha ao deserializar mensagem SQS: "
                "queue_url=%s, body=%s, erro=%s",
                self._queue_url,
                body[:200],
                str(exc),
            )
            return None

        logger.info(
            "Mensagem recebida da fila SQS: queue_url=%s, "
            "site_id=%s, cycle_id=%s, url=%s",
            self._queue_url,
            processing_message.site_id,
            processing_message.cycle_id,
            processing_message.url,
        )

        return (processing_message, receipt_handle)

    async def delete_message(self, receipt_handle: str) -> None:
        """Deleta mensagem após processamento bem-sucedido.

        Remove a mensagem da fila SQS utilizando o receipt_handle
        obtido durante o recebimento.

        Args:
            receipt_handle: Handle de recebimento da mensagem.

        Raises:
            ClientError: Se a operação de deleção falhar no SQS.
        """
        try:
            await asyncio.to_thread(
                self._client.delete_message,
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Falha ao deletar mensagem da fila SQS: "
                "queue_url=%s, receipt_handle=%s, erro=%s",
                self._queue_url,
                receipt_handle[:50],
                str(exc),
            )
            raise

        logger.info(
            "Mensagem deletada da fila SQS: queue_url=%s, "
            "receipt_handle=%s",
            self._queue_url,
            receipt_handle[:50],
        )

    async def extend_visibility(
        self,
        receipt_handle: str,
        additional_seconds: int = 60,
    ) -> None:
        """Renova visibility timeout para evitar reprocessamento.

        Estende o tempo que a mensagem permanece invisível para
        outros consumidores enquanto o processamento continua.

        Args:
            receipt_handle: Handle de recebimento da mensagem.
            additional_seconds: Segundos adicionais de invisibilidade.
                Padrão: 60.

        Raises:
            ClientError: Se a operação de extensão falhar no SQS.
        """
        try:
            await asyncio.to_thread(
                self._client.change_message_visibility,
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=additional_seconds,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Falha ao estender visibility timeout: "
                "queue_url=%s, receipt_handle=%s, "
                "additional_seconds=%d, erro=%s",
                self._queue_url,
                receipt_handle[:50],
                additional_seconds,
                str(exc),
            )
            raise

        logger.info(
            "Visibility timeout estendido: queue_url=%s, "
            "receipt_handle=%s, additional_seconds=%d",
            self._queue_url,
            receipt_handle[:50],
            additional_seconds,
        )
