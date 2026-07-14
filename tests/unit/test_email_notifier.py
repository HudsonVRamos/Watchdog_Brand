"""Testes unitários para EventNotificationHandler (ComplianceEmailNotifier via EventBridge).

Valida o comportamento TARGET do handler de notificações por evento:
- Email duplicado (mesmo cycle_id + target_url) é descartado sem reenvio
- DLQ recebe payload após 3 falhas consecutivas de envio

Estes testes definem o contrato TARGET para a task 6.3.
A implementação real será em brand_watchdog/events/notification_handler.py.

Requirements: 6.3, 6.4, 6.6
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.events.models import ComplianceCompletedEvent


# --- Implementação TARGET do EventNotificationHandler ---


class EventNotificationHandlerTarget:
    """Handler de notificações acionado por eventos ComplianceCompleted.

    Consome eventos do EventBridge, verifica deduplicação e envia
    emails via SES com retry. Registra na DLQ em caso de falha total.

    Esta é a implementação TARGET (contrato) para os testes unitários.
    A implementação real será criada na task 6.3.
    """

    def __init__(
        self,
        dedup_repository: AsyncMock,
        email_sender: AsyncMock,
        dlq_publisher: AsyncMock,
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

        1. Verifica deduplicação (cycle_id + target_url)
        2. Se duplicado: descarta silenciosamente
        3. Se novo: tenta enviar email com retry
        4. Se todas tentativas falharem: registra na DLQ

        Args:
            event: Evento de compliance completada.

        Returns:
            True se email enviado ou evento descartado (duplicado).
            False se falhou e foi para DLQ.
        """
        # Verificar deduplicação
        is_duplicate = await self._dedup_repo.exists(
            cycle_id=event.cycle_id,
            target_url=event.target_url,
        )
        if is_duplicate:
            return True  # Descartado silenciosamente

        # Tentar envio com retry
        for attempt in range(self._max_retries):
            try:
                await self._email_sender.send_compliance_email(
                    event
                )
                # Registrar na tabela de dedup após sucesso
                await self._dedup_repo.register(
                    cycle_id=event.cycle_id,
                    target_url=event.target_url,
                )
                return True
            except Exception:
                if attempt < self._max_retries - 1:
                    delay = self._backoff_delays[
                        min(attempt, len(self._backoff_delays) - 1)
                    ]
                    await asyncio.sleep(delay)

        # Todas tentativas falharam → DLQ
        await self._dlq.send(
            payload={
                "event": event.to_event_detail(),
                "failure_reason": (
                    f"Falha após {self._max_retries} tentativas"
                ),
            }
        )
        return False


# --- Fixtures ---


@pytest.fixture
def mock_dedup_repo() -> AsyncMock:
    """Mock do repositório de deduplicação."""
    repo = AsyncMock()
    repo.exists = AsyncMock(return_value=False)
    repo.register = AsyncMock()
    return repo


@pytest.fixture
def mock_email_sender() -> AsyncMock:
    """Mock do serviço de envio de email."""
    sender = AsyncMock()
    sender.send_compliance_email = AsyncMock()
    return sender


@pytest.fixture
def mock_dlq() -> AsyncMock:
    """Mock do publisher da DLQ de notificações."""
    dlq = AsyncMock()
    dlq.send = AsyncMock()
    return dlq


@pytest.fixture
def handler(
    mock_dedup_repo: AsyncMock,
    mock_email_sender: AsyncMock,
    mock_dlq: AsyncMock,
) -> EventNotificationHandlerTarget:
    """Cria instância do handler com dependências mockadas."""
    return EventNotificationHandlerTarget(
        dedup_repository=mock_dedup_repo,
        email_sender=mock_email_sender,
        dlq_publisher=mock_dlq,
        max_retries=3,
        backoff_delays=[0, 0, 0],  # Sem delay nos testes
    )


def _make_event(
    cycle_id: str = "cycle-001",
    target_url: str = "https://example.com/partner",
) -> ComplianceCompletedEvent:
    """Cria um evento ComplianceCompleted para testes."""
    return ComplianceCompletedEvent(
        site_id="site-uuid-001",
        cycle_id=cycle_id,
        target_url=target_url,
        brand="sky_plus",
        overall_status="non_compliant",
        rule_results=[
            {
                "rule_id": f"rule_{i}",
                "status": "PASS" if i != 0 else "FAIL",
                "confidence": 90,
                "description": f"Descrição da regra {i}",
            }
            for i in range(6)
        ],
        screenshot_s3_key="screenshots/cycle-001/ss-001.png",
        analyzed_at="2024-07-10T14:30:00Z",
    )


# --- Testes: Email duplicado descartado ---


class TestEmailDuplicadoDescartado:
    """Testes para Req 6.6: Evento duplicado é descartado sem reenvio.

    IF o ComplianceEmailNotifier receber um Evento_ComplianceCompleted
    duplicado (mesmo cycle_id e target_url), THEN SHALL descartar o
    evento sem reenviar o email.
    """

    @pytest.mark.asyncio
    async def test_evento_duplicado_nao_envia_email(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
    ):
        """Evento duplicado NÃO aciona envio de email.

        Validates: Requirements 6.6
        """
        # Simular que o evento já foi processado
        mock_dedup_repo.exists.return_value = True

        event = _make_event()
        result = await handler.handle_event(event)

        assert result is True
        mock_email_sender.send_compliance_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_evento_duplicado_retorna_true(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
    ):
        """Evento duplicado retorna True (descartado com sucesso).

        Validates: Requirements 6.6
        """
        mock_dedup_repo.exists.return_value = True

        event = _make_event()
        result = await handler.handle_event(event)

        assert result is True

    @pytest.mark.asyncio
    async def test_evento_duplicado_nao_registra_na_dlq(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_dlq: AsyncMock,
    ):
        """Evento duplicado NÃO vai para a DLQ.

        Validates: Requirements 6.6
        """
        mock_dedup_repo.exists.return_value = True

        event = _make_event()
        await handler.handle_event(event)

        mock_dlq.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_verifica_cycle_id_e_target_url(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
    ):
        """Deduplicação verifica combinação cycle_id + target_url.

        Validates: Requirements 6.6
        """
        mock_dedup_repo.exists.return_value = True

        event = _make_event(
            cycle_id="cycle-xyz",
            target_url="https://site.com/page",
        )
        await handler.handle_event(event)

        mock_dedup_repo.exists.assert_called_once_with(
            cycle_id="cycle-xyz",
            target_url="https://site.com/page",
        )

    @pytest.mark.asyncio
    async def test_evento_novo_registra_dedup_apos_envio(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
    ):
        """Evento novo registra dedup após envio bem-sucedido.

        Validates: Requirements 6.6
        """
        mock_dedup_repo.exists.return_value = False

        event = _make_event(
            cycle_id="cycle-new",
            target_url="https://new-site.com",
        )
        await handler.handle_event(event)

        mock_dedup_repo.register.assert_called_once_with(
            cycle_id="cycle-new",
            target_url="https://new-site.com",
        )


# --- Testes: DLQ recebe payload após 3 falhas ---


class TestDLQRecebePayloadApos3Falhas:
    """Testes para Req 6.4: DLQ recebe payload após 3 falhas.

    IF todas as 3 tentativas de envio falharem, THEN registrar o
    evento na DLQ contendo payload original e motivo da falha.
    """

    @pytest.mark.asyncio
    async def test_dlq_recebe_evento_apos_3_falhas(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
        mock_dlq: AsyncMock,
    ):
        """DLQ recebe o payload após 3 tentativas falhadas.

        Validates: Requirements 6.4
        """
        mock_dedup_repo.exists.return_value = False
        mock_email_sender.send_compliance_email.side_effect = (
            ConnectionError("SES indisponível")
        )

        event = _make_event()
        result = await handler.handle_event(event)

        assert result is False
        mock_dlq.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_dlq_payload_contem_evento_original(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
        mock_dlq: AsyncMock,
    ):
        """Payload na DLQ contém os dados do evento original.

        Validates: Requirements 6.4
        """
        mock_dedup_repo.exists.return_value = False
        mock_email_sender.send_compliance_email.side_effect = (
            ConnectionError("SES timeout")
        )

        event = _make_event(
            cycle_id="cycle-dlq-test",
            target_url="https://failed-site.com",
        )
        await handler.handle_event(event)

        # Verificar que o payload contém os dados do evento
        call_kwargs = mock_dlq.send.call_args[1]
        payload = call_kwargs["payload"]
        assert payload["event"]["cycle_id"] == "cycle-dlq-test"
        assert (
            payload["event"]["target_url"]
            == "https://failed-site.com"
        )

    @pytest.mark.asyncio
    async def test_dlq_payload_contem_motivo_da_falha(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
        mock_dlq: AsyncMock,
    ):
        """Payload na DLQ contém o motivo da falha.

        Validates: Requirements 6.4
        """
        mock_dedup_repo.exists.return_value = False
        mock_email_sender.send_compliance_email.side_effect = (
            ConnectionError("SES error")
        )

        event = _make_event()
        await handler.handle_event(event)

        call_kwargs = mock_dlq.send.call_args[1]
        payload = call_kwargs["payload"]
        assert "failure_reason" in payload
        assert "3" in payload["failure_reason"]

    @pytest.mark.asyncio
    async def test_email_sender_chamado_3_vezes_antes_da_dlq(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
        mock_dlq: AsyncMock,
    ):
        """Email sender é chamado exatamente 3 vezes antes de ir para DLQ.

        Validates: Requirements 6.3
        """
        mock_dedup_repo.exists.return_value = False
        mock_email_sender.send_compliance_email.side_effect = (
            ConnectionError("Falha persistente")
        )

        event = _make_event()
        await handler.handle_event(event)

        assert (
            mock_email_sender.send_compliance_email.call_count == 3
        )
        mock_dlq.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_nao_registrada_se_envio_falha(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
        mock_dlq: AsyncMock,
    ):
        """Dedup NÃO é registrada se todas tentativas de envio falharem.

        Validates: Requirements 6.6
        """
        mock_dedup_repo.exists.return_value = False
        mock_email_sender.send_compliance_email.side_effect = (
            ConnectionError("Falha total")
        )

        event = _make_event()
        await handler.handle_event(event)

        mock_dedup_repo.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_sucesso_na_segunda_tentativa_nao_vai_para_dlq(
        self,
        handler: EventNotificationHandlerTarget,
        mock_dedup_repo: AsyncMock,
        mock_email_sender: AsyncMock,
        mock_dlq: AsyncMock,
    ):
        """Sucesso na 2ª tentativa NÃO registra na DLQ.

        Validates: Requirements 6.3
        """
        mock_dedup_repo.exists.return_value = False
        # Falha na 1ª, sucesso na 2ª
        mock_email_sender.send_compliance_email.side_effect = [
            ConnectionError("Falha temporária"),
            None,  # sucesso
        ]

        event = _make_event()
        result = await handler.handle_event(event)

        assert result is True
        mock_dlq.send.assert_not_called()
        mock_dedup_repo.register.assert_called_once()
