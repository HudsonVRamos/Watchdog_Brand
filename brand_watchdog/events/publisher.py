"""Publisher de eventos no AWS EventBridge.

Responsável por publicar eventos de compliance completada no
EventBridge, com retry exponencial e validação de payload.
Falhas de publicação NÃO impedem conclusão do processamento.
"""

from __future__ import annotations

import asyncio
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from brand_watchdog.config import EventConfig
from brand_watchdog.events.models import ComplianceCompletedEvent

logger = logging.getLogger(__name__)

# Limite máximo de payload do EventBridge: 256 KB
_MAX_PAYLOAD_BYTES = 256 * 1024


class EventPublisher:
    """Publica eventos de compliance no AWS EventBridge.

    Envia eventos ComplianceCompleted com retry exponencial
    e validação de tamanho de payload. Nunca propaga exceções
    ao chamador — sempre retorna bool indicando sucesso/falha.

    Args:
        config: Configuração do EventBridge. Se não fornecida,
            usa valores padrão.
    """

    def __init__(
        self,
        config: EventConfig | None = None,
    ) -> None:
        self._config = config or EventConfig()
        self._client = boto3.client(
            "events",
            region_name=self._config.region,
        )

    async def publish_compliance_completed(
        self,
        event: ComplianceCompletedEvent,
    ) -> bool:
        """Publica evento ComplianceCompleted no EventBridge.

        Valida tamanho do payload antes de publicar. Realiza
        retry com backoff exponencial (1s, 2s, 4s) em caso de
        falha. Nunca levanta exceções ao chamador.

        Args:
            event: Evento de compliance completada a publicar.

        Returns:
            True se publicado com sucesso, False caso contrário.
        """
        # Serializar evento para JSON
        try:
            detail_json = event.to_json()
        except Exception as exc:
            logger.error(
                "Falha ao serializar evento ComplianceCompleted: "
                "site_id=%s, cycle_id=%s, erro=%s",
                event.site_id,
                event.cycle_id,
                str(exc),
            )
            return False

        # Validar tamanho do payload (< 256KB)
        payload_size = len(detail_json.encode("utf-8"))
        if payload_size >= _MAX_PAYLOAD_BYTES:
            logger.error(
                "Payload do evento excede limite de 256KB: "
                "site_id=%s, cycle_id=%s, "
                "tamanho=%d bytes, limite=%d bytes",
                event.site_id,
                event.cycle_id,
                payload_size,
                _MAX_PAYLOAD_BYTES,
            )
            return False

        # Retry com backoff exponencial: 1s, 2s, 4s
        backoff_delays = [1, 2, 4]
        max_retries = self._config.max_retries

        for attempt in range(max_retries):
            try:
                response = await asyncio.to_thread(
                    self._client.put_events,
                    Entries=[
                        {
                            "Source": self._config.source,
                            "DetailType": (
                                self._config.detail_type_compliance
                            ),
                            "Detail": detail_json,
                            "EventBusName": (
                                self._config.event_bus_name
                            ),
                        }
                    ],
                )

                # Verificar se houve falhas na resposta
                failed_count = response.get(
                    "FailedEntryCount", 0
                )
                if failed_count == 0:
                    logger.info(
                        "Evento ComplianceCompleted publicado: "
                        "site_id=%s, cycle_id=%s",
                        event.site_id,
                        event.cycle_id,
                    )
                    return True

                # Falha parcial reportada pelo EventBridge
                entries = response.get("Entries", [])
                error_msg = (
                    entries[0].get("ErrorMessage", "desconhecido")
                    if entries
                    else "desconhecido"
                )
                logger.warning(
                    "EventBridge reportou falha na entrada: "
                    "site_id=%s, cycle_id=%s, "
                    "tentativa=%d/%d, erro=%s",
                    event.site_id,
                    event.cycle_id,
                    attempt + 1,
                    max_retries,
                    error_msg,
                )

            except (ClientError, BotoCoreError) as exc:
                logger.warning(
                    "Falha ao publicar evento no EventBridge: "
                    "site_id=%s, cycle_id=%s, "
                    "tentativa=%d/%d, erro=%s",
                    event.site_id,
                    event.cycle_id,
                    attempt + 1,
                    max_retries,
                    str(exc),
                )
            except Exception as exc:
                logger.warning(
                    "Erro inesperado ao publicar evento: "
                    "site_id=%s, cycle_id=%s, "
                    "tentativa=%d/%d, erro=%s",
                    event.site_id,
                    event.cycle_id,
                    attempt + 1,
                    max_retries,
                    str(exc),
                )

            # Aguardar antes do próximo retry (exceto na última)
            if attempt < max_retries - 1:
                delay = backoff_delays[
                    min(attempt, len(backoff_delays) - 1)
                ]
                await asyncio.sleep(delay)

        # Esgotou todas as tentativas
        logger.error(
            "Evento ComplianceCompleted não publicado após "
            "%d tentativas: site_id=%s, cycle_id=%s",
            max_retries,
            event.site_id,
            event.cycle_id,
        )
        return False
