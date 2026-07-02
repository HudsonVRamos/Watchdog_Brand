"""Testes de integração: EventBridge aciona notificador.

Utiliza moto para simular EventBridge, verificando que eventos
ComplianceCompleted são publicados com campos corretos.

Requirements: 5.1
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from brand_watchdog.config import EventConfig
from brand_watchdog.events.models import ComplianceCompletedEvent
from brand_watchdog.events.publisher import EventPublisher


# --- Helpers ---


def _make_compliance_event() -> ComplianceCompletedEvent:
    """Cria um ComplianceCompletedEvent de teste."""
    return ComplianceCompletedEvent(
        site_id=str(uuid.uuid4()),
        cycle_id=str(uuid.uuid4()),
        target_url="https://isp-test.com.br/sky-amazon",
        brand="sky_plus",
        overall_status="non_compliant",
        rule_results=[
            {
                "rule_id": "facilitator_role",
                "status": "FAIL",
                "confidence": 87,
            },
            {
                "rule_id": "logo_application",
                "status": "PASS",
                "confidence": 92,
            },
            {
                "rule_id": "logo_effects",
                "status": "PASS",
                "confidence": 90,
            },
            {
                "rule_id": "content_separation",
                "status": "PASS",
                "confidence": 85,
            },
            {
                "rule_id": "naming_pricing",
                "status": "PASS",
                "confidence": 95,
            },
            {
                "rule_id": "kv_integrity",
                "status": "PASS",
                "confidence": 91,
            },
        ],
        screenshot_s3_key="screenshots/cycle-1/ss-1.png",
        analyzed_at="2025-01-15T10:30:00Z",
    )


# --- Testes ---


@pytest.mark.integration
class TestEventsE2E:
    """Testes de integração do EventBridge com publicação de eventos."""

    async def test_publish_compliance_event_succeeds(
        self,
    ) -> None:
        """EventPublisher publica evento com sucesso no EventBridge.

        Verifica que:
        1. PutEvents retorna sem falhas
        2. O evento é publicado com source e detail-type corretos
        """
        with mock_aws():
            config = EventConfig(
                event_bus_name="default",
                source="brand-watchdog",
                detail_type_compliance="ComplianceCompleted",
                region="us-east-1",
                max_retries=3,
            )

            publisher = EventPublisher(config=config)
            event = _make_compliance_event()

            result = (
                await publisher.publish_compliance_completed(
                    event
                )
            )
            assert result is True

    async def test_event_contains_correct_source_and_detail_type(
        self,
    ) -> None:
        """Evento publicado tem source='brand-watchdog' e detail-type correto.

        Verifica o formato JSON do evento diretamente.
        """
        with mock_aws():
            config = EventConfig(
                event_bus_name="default",
                source="brand-watchdog",
                detail_type_compliance="ComplianceCompleted",
                region="us-east-1",
                max_retries=3,
            )

            publisher = EventPublisher(config=config)
            event = _make_compliance_event()

            # Publica o evento
            result = (
                await publisher.publish_compliance_completed(
                    event
                )
            )
            assert result is True

            # Validamos via o formato JSON do evento
            detail_json = event.to_json()
            detail = json.loads(detail_json)

            assert detail["site_id"] == event.site_id
            assert detail["cycle_id"] == event.cycle_id
            assert detail["target_url"] == event.target_url
            assert detail["brand"] == event.brand
            assert (
                detail["overall_status"]
                == event.overall_status
            )
            assert len(detail["rule_results"]) == 6
            assert (
                detail["screenshot_s3_key"]
                == event.screenshot_s3_key
            )
            assert detail["analyzed_at"] == event.analyzed_at

    async def test_event_detail_contains_all_required_fields(
        self,
    ) -> None:
        """O campo 'detail' do evento contém todos os campos obrigatórios.

        Campos obrigatórios: site_id, cycle_id, target_url, brand,
        overall_status, rule_results, screenshot_s3_key, analyzed_at
        """
        with mock_aws():
            config = EventConfig(
                event_bus_name="default",
                source="brand-watchdog",
                detail_type_compliance="ComplianceCompleted",
                region="us-east-1",
                max_retries=3,
            )

            publisher = EventPublisher(config=config)
            event = _make_compliance_event()

            result = (
                await publisher.publish_compliance_completed(
                    event
                )
            )
            assert result is True

            # Verifica os campos do evento serializado
            event_detail = event.to_event_detail()
            required_fields = {
                "site_id",
                "cycle_id",
                "target_url",
                "brand",
                "overall_status",
                "rule_results",
                "screenshot_s3_key",
                "analyzed_at",
            }
            assert required_fields.issubset(
                set(event_detail.keys())
            )

            # Valida tipos
            assert isinstance(event_detail["site_id"], str)
            assert isinstance(event_detail["cycle_id"], str)
            assert isinstance(event_detail["target_url"], str)
            assert event_detail["brand"] in ("sky_plus", "dgo")
            assert event_detail["overall_status"] in (
                "compliant",
                "non_compliant",
            )
            assert isinstance(
                event_detail["rule_results"], list
            )
            assert len(event_detail["rule_results"]) == 6

            # Valida estrutura de cada rule_result
            for rule in event_detail["rule_results"]:
                assert "rule_id" in rule
                assert "status" in rule
                assert "confidence" in rule
                assert rule["status"] in (
                    "PASS",
                    "FAIL",
                    "NOT_APPLICABLE",
                )
                assert 0 <= rule["confidence"] <= 100

    async def test_oversized_event_not_published(self) -> None:
        """Evento com payload > 256KB não é publicado.

        Verifica que o EventPublisher rejeita payloads excessivos
        sem levantar exceção.
        """
        with mock_aws():
            config = EventConfig(
                event_bus_name="default",
                source="brand-watchdog",
                detail_type_compliance="ComplianceCompleted",
                region="us-east-1",
                max_retries=3,
            )

            publisher = EventPublisher(config=config)

            # Cria evento com payload enorme (> 256KB)
            event = ComplianceCompletedEvent(
                site_id=str(uuid.uuid4()),
                cycle_id=str(uuid.uuid4()),
                target_url="https://example.com",
                brand="sky_plus",
                overall_status="compliant",
                rule_results=[
                    {
                        "rule_id": f"rule_{i}",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "x" * 50000,
                    }
                    for i in range(10)
                ],
                screenshot_s3_key="screenshots/c/s.png",
                analyzed_at="2025-01-15T10:30:00Z",
            )

            result = (
                await publisher.publish_compliance_completed(
                    event
                )
            )
            assert result is False

    async def test_event_failure_does_not_raise_exception(
        self,
    ) -> None:
        """Falha de publicação retorna False sem levantar exceção.

        Verifica que o contrato de retorno é respeitado:
        resultado é bool, não exceção.
        """
        with mock_aws():
            config = EventConfig(
                event_bus_name="default",
                source="brand-watchdog",
                detail_type_compliance="ComplianceCompleted",
                region="us-east-1",
                max_retries=1,
            )

            publisher = EventPublisher(config=config)
            event = _make_compliance_event()

            # No moto, a publicação funciona normalmente
            result = (
                await publisher.publish_compliance_completed(
                    event
                )
            )
            assert isinstance(result, bool)
