"""Publisher de mensagens na fila SQS.

Responsável por publicar mensagens de processamento na fila SQS
em batches de até 10 mensagens, com retry e timeout configuráveis.
"""

from __future__ import annotations

import asyncio
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from brand_watchdog.queue.messages import ProcessingMessage

logger = logging.getLogger(__name__)


class SQSPublisher:
    """Publica mensagens de processamento na fila SQS.

    Gerencia publicação em batches de 10 mensagens (limite do SQS),
    com retry exponencial e timeout global para a fase de publicação.

    Args:
        queue_url: URL da fila SQS de destino.
        region: Região AWS da fila. Padrão: "us-east-1".
    """

    def __init__(self, queue_url: str, region: str = "us-east-1") -> None:
        self._queue_url = queue_url
        self._region = region
        self._client = boto3.client("sqs", region_name=region)

    async def publish_batch(
        self, messages: list[ProcessingMessage]
    ) -> tuple[int, int]:
        """Publica um batch de até 10 mensagens na fila SQS.

        Utiliza SendMessageBatch do SQS para envio eficiente.
        Cada mensagem recebe um ID único para rastreamento no batch.

        Args:
            messages: Lista de mensagens a publicar (máximo 10).

        Returns:
            Tupla (sucessos, falhas) com contagem de cada resultado.

        Raises:
            ValueError: Se o batch contiver mais de 10 mensagens.
        """
        if not messages:
            return (0, 0)

        if len(messages) > 10:
            raise ValueError(
                "Batch excede limite de 10 mensagens: "
                f"{len(messages)} recebidas."
            )

        entries = [
            {
                "Id": str(idx),
                "MessageBody": msg.to_json(),
            }
            for idx, msg in enumerate(messages)
        ]

        try:
            response = self._client.send_message_batch(
                QueueUrl=self._queue_url,
                Entries=entries,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Falha ao publicar batch na fila SQS: queue_url=%s, "
                "mensagens=%d, erro=%s",
                self._queue_url,
                len(messages),
                str(exc),
            )
            return (0, len(messages))

        successful = response.get("Successful", [])
        failed = response.get("Failed", [])

        if failed:
            for entry in failed:
                logger.error(
                    "Mensagem falhou no batch SQS: id=%s, code=%s, message=%s",
                    entry.get("Id"),
                    entry.get("Code"),
                    entry.get("Message"),
                )

        if successful:
            logger.info(
                "Batch publicado na fila SQS: queue_url=%s, "
                "sucessos=%d, falhas=%d",
                self._queue_url,
                len(successful),
                len(failed),
            )

        return (len(successful), len(failed))

    async def publish_all(
        self,
        messages: list[ProcessingMessage],
        max_retries: int = 3,
        timeout_minutes: int = 5,
    ) -> tuple[int, int]:
        """Publica todas as mensagens em batches de 10 com retry.

        Divide as mensagens em batches de 10, publica cada batch
        e realiza retry com exponential backoff para mensagens que
        falharam. Respeita um timeout global de publicação.

        Args:
            messages: Lista completa de mensagens a publicar.
            max_retries: Número máximo de tentativas por batch.
                Padrão: 3.
            timeout_minutes: Timeout global em minutos para a fase
                de publicação. Padrão: 5.

        Returns:
            Tupla (total_sucessos, total_falhas) consolidando
            resultados de todos os batches.
        """
        if not messages:
            return (0, 0)

        # Dividir mensagens em batches de 10
        batch_size = 10
        batches: list[list[ProcessingMessage]] = [
            messages[i:i + batch_size]
            for i in range(0, len(messages), batch_size)
        ]

        logger.info(
            "Iniciando publicação na fila SQS: total_mensagens=%d, "
            "batches=%d, queue_url=%s",
            len(messages),
            len(batches),
            self._queue_url,
        )

        # Inicializar progresso para recuperação em caso de timeout
        self._progress_success = 0
        self._progress_failures = 0

        try:
            total_success, total_failures = await asyncio.wait_for(
                self._publish_batches(batches, max_retries),
                timeout=timeout_minutes * 60,
            )
        except asyncio.TimeoutError:
            # Timeout atingido — registrar mensagens restantes
            # como falhas. _published_progress é atualizado
            # pela coroutine interna antes do timeout.
            total_success = self._progress_success
            total_failures = self._progress_failures
            published_so_far = total_success + total_failures
            remaining = len(messages) - published_so_far
            total_failures += remaining

            logger.error(
                "Timeout de %d minutos atingido na fase de publicação: "
                "publicados=%d, falhas_anteriores=%d, "
                "sites_não_publicados=%d, queue_url=%s",
                timeout_minutes,
                total_success,
                total_failures - remaining,
                remaining,
                self._queue_url,
            )

        logger.info(
            "Publicação na fila SQS concluída: "
            "total_sucessos=%d, total_falhas=%d",
            total_success,
            total_failures,
        )

        return (total_success, total_failures)

    async def _publish_batches(
        self,
        batches: list[list[ProcessingMessage]],
        max_retries: int,
    ) -> tuple[int, int]:
        """Publica todos os batches sequencialmente.

        Atualiza _progress_success e _progress_failures para
        permitir recuperação de estado em caso de timeout.

        Args:
            batches: Lista de batches a publicar.
            max_retries: Número máximo de tentativas por batch.

        Returns:
            Tupla (total_sucessos, total_falhas).
        """
        self._progress_success = 0
        self._progress_failures = 0

        for batch_idx, batch in enumerate(batches):
            success, failures = await self._publish_batch_with_retry(
                batch=batch,
                batch_idx=batch_idx,
                max_retries=max_retries,
            )
            self._progress_success += success
            self._progress_failures += failures

        return (self._progress_success, self._progress_failures)

    async def _publish_batch_with_retry(
        self,
        batch: list[ProcessingMessage],
        batch_idx: int,
        max_retries: int,
    ) -> tuple[int, int]:
        """Publica um batch com retry exponencial para falhas.

        Tenta publicar o batch e, em caso de falhas parciais,
        realiza retry apenas das mensagens que falharam, com
        backoff exponencial (1s, 2s, 4s).

        Args:
            batch: Lista de mensagens do batch atual.
            batch_idx: Índice do batch para logging.
            max_retries: Número máximo de tentativas.

        Returns:
            Tupla (sucessos, falhas) do batch após retries.
        """
        pending = list(batch)
        total_success = 0
        backoff_delays = [1, 2, 4]  # Exponential backoff: 1s, 2s, 4s

        for attempt in range(max_retries):
            success, failure_count = await self.publish_batch(pending)
            total_success += success

            # Se todas tiveram sucesso, terminamos
            if failure_count == 0:
                return (total_success, 0)

            # Identificar mensagens que falharam para retry
            # Quando publish_batch retorna falhas, sabemos que as
            # últimas `failure_count` mensagens do pending falharam
            # (baseado na ordem de resposta do SQS)
            failed_messages = pending[success:]
            pending = failed_messages

            # Se é a última tentativa, não faz sleep
            if attempt < max_retries - 1:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                logger.warning(
                    "Retry de batch SQS: batch_idx=%d, tentativa=%d/%d, "
                    "falhas=%d, próximo_retry_em=%ds",
                    batch_idx,
                    attempt + 1,
                    max_retries,
                    failure_count,
                    delay,
                )
                await asyncio.sleep(delay)

        # Esgotou todas as tentativas — registrar falhas definitivas
        remaining_failures = len(pending)
        if remaining_failures > 0:
            for msg in pending:
                logger.error(
                    "Mensagem não publicada após %d tentativas: "
                    "site_id=%s, cycle_id=%s, url=%s",
                    max_retries,
                    msg.site_id,
                    msg.cycle_id,
                    msg.url,
                )

        return (total_success, remaining_failures)
