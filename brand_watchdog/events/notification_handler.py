"""Handler de notificações acionado por eventos ComplianceCompleted.

Consome eventos do EventBridge, verifica deduplicação via tabela
notification_dedup e envia emails de compliance via SES com retry
e backoff. Registra na DLQ de notificações em caso de falha total.

Garante isolamento: falha de um evento não bloqueia outros.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from brand_watchdog.events.models import ComplianceCompletedEvent

logger = logging.getLogger(__name__)


class DedupRepository(Protocol):
    """Protocolo para repositório de deduplicação de notificações."""

    async def exists(
        self, *, cycle_id: str, target_url: str
    ) -> bool:
        """Verifica se o par (cycle_id, target_url) já foi processado."""
        ...

    async def register(
        self, *, cycle_id: str, target_url: str
    ) -> None:
        """Registra o par (cycle_id, target_url) como processado."""
        ...


class EmailSender(Protocol):
    """Protocolo para serviço de envio de email de compliance."""

    async def send_compliance_email(
        self, event: ComplianceCompletedEvent
    ) -> None:
        """Envia email de compliance baseado no evento.

        Raises:
            Exception: Se o envio falhar.
        """
        ...


class DLQPublisher(Protocol):
    """Protocolo para publisher da DLQ de notificações."""

    async def send(self, *, payload: dict) -> None:
        """Envia payload para a DLQ de notificações."""
        ...


class EventNotificationHandler:
    """Handler de notificações acionado por eventos ComplianceCompleted.

    Consome eventos do EventBridge, verifica deduplicação e envia
    emails via SES com retry. Registra na DLQ em caso de falha total.

    O handler garante:
    - Deduplicação via tabela notification_dedup (UNIQUE cycle_id + target_url)
    - Retry 3x com backoff (30s, 60s, 120s) para envio de email
    - Registro em DLQ se todas tentativas falharem
    - Isolamento: falha de um evento não bloqueia outros

    Args:
        dedup_repository: Repositório para verificar/registrar deduplicação.
        email_sender: Serviço de envio de email de compliance.
        dlq_publisher: Publisher para a DLQ de notificações.
        max_retries: Número máximo de tentativas de envio (padrão: 3).
        backoff_delays: Lista de delays em segundos entre tentativas
            (padrão: [30, 60, 120]).
    """

    def __init__(
        self,
        dedup_repository: DedupRepository,
        email_sender: EmailSender,
        dlq_publisher: DLQPublisher,
        max_retries: int = 3,
        backoff_delays: list[int] | None = None,
    ) -> None:
        self._dedup_repo = dedup_repository
        self._email_sender = email_sender
        self._dlq = dlq_publisher
        self._max_retries = max_retries
        self._backoff_delays = backoff_delays or [30, 60, 120]

    async def handle_event(
        self,
        event: ComplianceCompletedEvent,
    ) -> bool:
        """Processa um evento ComplianceCompleted.

        Fluxo:
        1. Verifica deduplicação (cycle_id + target_url)
        2. Se duplicado: descarta silenciosamente, retorna True
        3. Se novo: tenta enviar email com retry e backoff
        4. Se envio com sucesso: registra na tabela de dedup
        5. Se todas tentativas falharem: registra na DLQ, retorna False

        Args:
            event: Evento de compliance completada.

        Returns:
            True se email enviado com sucesso ou evento descartado
            (duplicado). False se falhou e foi para DLQ.
        """
        # 1. Verificar deduplicação (cycle_id + target_url)
        try:
            is_duplicate = await self._dedup_repo.exists(
                cycle_id=event.cycle_id,
                target_url=event.target_url,
            )
        except Exception as exc:
            logger.error(
                "Erro ao verificar deduplicação: "
                "cycle_id=%s, target_url=%s, erro=%s",
                event.cycle_id,
                event.target_url,
                str(exc),
            )
            # Em caso de erro na verificação, tenta enviar
            is_duplicate = False

        # 2. Se duplicado, descarta silenciosamente
        if is_duplicate:
            logger.info(
                "Evento duplicado descartado: "
                "cycle_id=%s, target_url=%s",
                event.cycle_id,
                event.target_url,
            )
            return True

        # 3. Tentar envio com retry e backoff
        for attempt in range(self._max_retries):
            try:
                await self._email_sender.send_compliance_email(
                    event
                )

                # 4. Registrar na tabela de dedup após sucesso
                await self._dedup_repo.register(
                    cycle_id=event.cycle_id,
                    target_url=event.target_url,
                )

                logger.info(
                    "Notificação enviada com sucesso: "
                    "cycle_id=%s, target_url=%s",
                    event.cycle_id,
                    event.target_url,
                )
                return True

            except Exception as exc:
                logger.warning(
                    "Falha ao enviar notificação "
                    "(tentativa %d/%d): "
                    "cycle_id=%s, target_url=%s, erro=%s",
                    attempt + 1,
                    self._max_retries,
                    event.cycle_id,
                    event.target_url,
                    str(exc),
                )
                # Aguardar backoff antes do próximo retry
                # (exceto na última tentativa)
                if attempt < self._max_retries - 1:
                    delay = self._backoff_delays[
                        min(
                            attempt,
                            len(self._backoff_delays) - 1,
                        )
                    ]
                    await asyncio.sleep(delay)

        # 5. Todas tentativas falharam → registrar na DLQ
        logger.error(
            "Falha definitiva ao enviar notificação após "
            "%d tentativas: cycle_id=%s, target_url=%s. "
            "Registrando na DLQ.",
            self._max_retries,
            event.cycle_id,
            event.target_url,
        )

        try:
            await self._dlq.send(
                payload={
                    "event": event.to_event_detail(),
                    "failure_reason": (
                        f"Falha após {self._max_retries} tentativas"
                    ),
                }
            )
        except Exception as exc:
            logger.error(
                "Erro ao registrar na DLQ: "
                "cycle_id=%s, target_url=%s, erro=%s",
                event.cycle_id,
                event.target_url,
                str(exc),
            )

        return False
