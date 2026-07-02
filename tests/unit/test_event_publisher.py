"""Testes unitários para EventPublisher.

Valida o comportamento do EventPublisher conforme requisitos:
- Evento > 256KB NÃO é publicado (log ERROR, retorna False)
- Falha no EventBridge NÃO impede processamento (retorna False sem exceção)

Requirements: 5.4, 5.6
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from brand_watchdog.config import EventConfig
from brand_watchdog.events.models import ComplianceCompletedEvent
from brand_watchdog.events.publisher import EventPublisher


# --- Helpers ---


def _make_event(
    payload_size_override: int | None = None,
) -> ComplianceCompletedEvent:
    """Cria um ComplianceCompletedEvent válido para testes.

    Se payload_size_override for fornecido, cria um evento com
    description grande o suficiente para atingir o tamanho desejado.
    """
    rule_results = [
        {
            "rule_id": f"rule_{i}",
            "status": "PASS",
            "confidence": 90,
        }
        for i in range(6)
    ]

    event = ComplianceCompletedEvent(
        site_id="site-uuid-001",
        cycle_id="cycle-uuid-001",
        target_url="https://example.com/partner",
        brand="sky_plus",
        overall_status="compliant",
        rule_results=rule_results,
        screenshot_s3_key="screenshots/cycle-uuid-001/ss-001.png",
        analyzed_at="2024-07-10T14:30:00Z",
    )

    if payload_size_override is not None:
        # Inflar o evento para atingir o tamanho desejado
        current_size = len(event.to_json().encode("utf-8"))
        needed = payload_size_override - current_size
        if needed > 0:
            # Adicionar descrição grande na primeira regra
            event.rule_results[0]["description"] = "x" * needed
    return event


def _make_event_over_256kb() -> ComplianceCompletedEvent:
    """Cria um evento com payload > 256KB."""
    return _make_event(payload_size_override=256 * 1024 + 100)


def _make_eventbridge_error() -> ClientError:
    """Cria um ClientError do EventBridge."""
    return ClientError(
        {
            "Error": {
                "Code": "InternalException",
                "Message": "Internal service error",
            }
        },
        "PutEvents",
    )


# --- Testes: Evento > 256KB não publicado ---


class TestPayloadExcede256KB:
    """Testes para Req 5.6: Evento > 256KB não é publicado.

    IF tamanho do payload > 256KB, THEN registrar ERROR no log
    e não publicar o evento, sem impedir conclusão do processamento.
    """

    @pytest.mark.asyncio
    async def test_evento_maior_que_256kb_retorna_false(self):
        """Evento com payload > 256KB retorna False sem publicar.

        Validates: Requirements 5.6
        """
        config = EventConfig()
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            event = _make_event_over_256kb()

            result = await publisher.publish_compliance_completed(
                event
            )

        assert result is False
        # put_events NÃO deve ser chamado
        mock_client.put_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_evento_maior_que_256kb_loga_error(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Evento > 256KB registra mensagem de ERROR no log.

        Validates: Requirements 5.6
        """
        config = EventConfig()
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            event = _make_event_over_256kb()

            with caplog.at_level(logging.ERROR):
                await publisher.publish_compliance_completed(event)

        # Verifica que o log contém mensagem de erro sobre tamanho
        assert any(
            "256KB" in record.message or "256" in record.message
            for record in caplog.records
            if record.levelno >= logging.ERROR
        )

    @pytest.mark.asyncio
    async def test_evento_menor_que_256kb_e_publicado(self):
        """Evento com payload < 256KB é publicado normalmente.

        Validates: Requirements 5.6
        """
        config = EventConfig()
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.put_events.return_value = {
                "FailedEntryCount": 0,
                "Entries": [{"EventId": "evt-001"}],
            }
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            event = _make_event()

            result = await publisher.publish_compliance_completed(
                event
            )

        assert result is True
        mock_client.put_events.assert_called_once()

    @pytest.mark.asyncio
    async def test_evento_256kb_nao_impede_processamento(self):
        """Evento > 256KB não lança exceção — processamento continua.

        Validates: Requirements 5.6
        """
        config = EventConfig()
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            event = _make_event_over_256kb()

            # Não deve lançar exceção
            result = await publisher.publish_compliance_completed(
                event
            )

        # Retorna False mas não impede processamento
        assert result is False


# --- Testes: EventBridge falha não impede processamento ---


class TestEventBridgeFalhaNaoImpedeProcessamento:
    """Testes para Req 5.4: Falha no EventBridge não impede processamento.

    Falha de publicação após todas as tentativas NÃO SHALL impedir
    a conclusão do processamento do site.
    """

    @pytest.mark.asyncio
    async def test_falha_eventbridge_retorna_false_sem_excecao(self):
        """Falha no EventBridge retorna False sem propagar exceção.

        Validates: Requirements 5.4
        """
        config = EventConfig()
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.put_events.side_effect = (
                _make_eventbridge_error()
            )
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            event = _make_event()

            # Não deve lançar exceção
            result = await publisher.publish_compliance_completed(
                event
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_falha_eventbridge_tenta_3_vezes(self):
        """Falha no EventBridge faz 3 tentativas antes de desistir.

        Validates: Requirements 5.4
        """
        config = EventConfig(max_retries=3)
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.put_events.side_effect = (
                _make_eventbridge_error()
            )
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            # Patch asyncio.sleep para não esperar de verdade
            with patch("asyncio.sleep", new_callable=AsyncMock):
                event = _make_event()
                await publisher.publish_compliance_completed(event)

        assert mock_client.put_events.call_count == 3

    @pytest.mark.asyncio
    async def test_falha_eventbridge_loga_error_apos_tentativas(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Falha após todas tentativas registra ERROR no log.

        Validates: Requirements 5.4
        """
        config = EventConfig(max_retries=3)
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.put_events.side_effect = (
                _make_eventbridge_error()
            )
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with caplog.at_level(logging.ERROR):
                    event = _make_event()
                    await publisher.publish_compliance_completed(
                        event
                    )

        # Verifica que log de erro final foi registrado
        error_records = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR
        ]
        assert len(error_records) >= 1
        assert any(
            "não publicado" in r.message or "cycle_id" in r.message
            for r in error_records
        )

    @pytest.mark.asyncio
    async def test_failed_entry_count_aciona_retry(self):
        """EventBridge retornando FailedEntryCount > 0 aciona retry.

        Validates: Requirements 5.4
        """
        config = EventConfig(max_retries=3)
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            # Todas as tentativas retornam FailedEntryCount=1
            mock_client.put_events.return_value = {
                "FailedEntryCount": 1,
                "Entries": [
                    {
                        "ErrorCode": "InternalFailure",
                        "ErrorMessage": "Service error",
                    }
                ],
            }
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                event = _make_event()
                result = (
                    await publisher.publish_compliance_completed(
                        event
                    )
                )

        assert result is False
        assert mock_client.put_events.call_count == 3

    @pytest.mark.asyncio
    async def test_sucesso_na_segunda_tentativa(self):
        """Sucesso na segunda tentativa retorna True.

        Validates: Requirements 5.4
        """
        config = EventConfig(max_retries=3)
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            # Falha na 1ª, sucesso na 2ª
            mock_client.put_events.side_effect = [
                _make_eventbridge_error(),
                {
                    "FailedEntryCount": 0,
                    "Entries": [{"EventId": "evt-001"}],
                },
            ]
            mock_boto.return_value = mock_client

            publisher = EventPublisher(config)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                event = _make_event()
                result = (
                    await publisher.publish_compliance_completed(
                        event
                    )
                )

        assert result is True
        assert mock_client.put_events.call_count == 2
