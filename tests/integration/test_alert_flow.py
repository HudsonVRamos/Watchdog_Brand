"""Testes de integração do fluxo de alertas.

Valida o pipeline completo de alertas:
- DetectionResult → AlertService → Email enviado com conteúdo correto
- Supressão de alertas duplicados
- Integração com SESProvider (boto3 mockado)
- Integração com SMTPProvider (aiosmtplib mockado)

Validates: Requirements 6.1, 6.2, 6.3
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.alerts.alert_service import AlertService, EmailProvider
from brand_watchdog.alerts.email_providers import SESProvider, SMTPProvider
from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import BoundingBox, DetectionResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeEmailProvider(EmailProvider):
    """Provedor de email falso que captura emails enviados."""

    def __init__(self) -> None:
        self.sent_emails: list[dict[str, str]] = []

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        sender: str,
    ) -> None:
        """Captura o email enviado ao invés de enviar de fato."""
        self.sent_emails.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "sender": sender,
            }
        )


class FakeDetectionStore:
    """Store falso para controlar detecções do ciclo anterior."""

    def __init__(
        self, previous_detections: list[DetectionResult] | None = None
    ) -> None:
        self._previous = previous_detections or []

    async def get_previous_cycle_detections(
        self, target_url: str
    ) -> list[DetectionResult]:
        """Retorna detecções pré-configuradas para supressão."""
        return [d for d in self._previous if d.target_url == target_url]


@pytest.fixture
def alert_config() -> AlertConfig:
    """Configuração padrão de alertas para testes."""
    return AlertConfig(
        provider="ses",
        ses_region="us-east-1",
        ses_sender="watchdog@example.com",
        recipients=["admin@example.com"],
        retry_attempts=1,
        retry_interval_seconds=0,
    )


@pytest.fixture
def sample_detection() -> DetectionResult:
    """DetectionResult realista com todos os campos populados."""
    return DetectionResult(
        target_url="https://suspicious-site.com/page",
        match_type="logo",
        confidence=85,
        bounding_box=BoundingBox(
            x_percent=10.5,
            y_percent=25.3,
            width_percent=15.0,
            height_percent=8.2,
        ),
        description="Logo da marca encontrado no cabeçalho do site",
        detected_at=datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc),
        screenshot_ref_id="screenshot-abc-123",
    )


@pytest.fixture
def fake_provider() -> FakeEmailProvider:
    """Provedor de email falso para captura de emails."""
    return FakeEmailProvider()


@pytest.fixture
def empty_detection_store() -> FakeDetectionStore:
    """Store sem detecções anteriores (sem supressão)."""
    return FakeDetectionStore(previous_detections=[])


# ---------------------------------------------------------------------------
# Testes de integração: fluxo completo de alerta
# ---------------------------------------------------------------------------


class TestAlertFlowIntegration:
    """Testes do pipeline completo: detecção → email enviado."""

    async def test_send_alert_delivers_to_all_recipients(
        self,
        alert_config: AlertConfig,
        sample_detection: DetectionResult,
        fake_provider: FakeEmailProvider,
        empty_detection_store: FakeDetectionStore,
    ) -> None:
        """Email deve ser enviado para todos os destinatários."""
        recipients = [
            "admin@example.com",
            "security@example.com",
            "legal@example.com",
        ]

        service = AlertService(
            config=alert_config,
            detection_store=empty_detection_store,
            email_provider=fake_provider,
        )

        result = await service.send_alert(sample_detection, recipients)

        assert result is True
        assert len(fake_provider.sent_emails) == 3
        sent_recipients = {
            e["recipient"] for e in fake_provider.sent_emails
        }
        assert sent_recipients == set(recipients)

    async def test_email_subject_contains_match_type_and_url(
        self,
        alert_config: AlertConfig,
        sample_detection: DetectionResult,
        fake_provider: FakeEmailProvider,
        empty_detection_store: FakeDetectionStore,
    ) -> None:
        """Subject do email deve conter tipo de match e URL do site."""
        service = AlertService(
            config=alert_config,
            detection_store=empty_detection_store,
            email_provider=fake_provider,
        )

        await service.send_alert(
            sample_detection, ["admin@example.com"]
        )

        email = fake_provider.sent_emails[0]
        subject = email["subject"]
        # Deve conter tipo de match (Logo para "logo")
        assert "Logo" in subject
        # Deve conter a URL do target
        assert sample_detection.target_url in subject

    async def test_email_body_contains_all_required_fields(
        self,
        alert_config: AlertConfig,
        sample_detection: DetectionResult,
        fake_provider: FakeEmailProvider,
        empty_detection_store: FakeDetectionStore,
    ) -> None:
        """Body do email deve conter URL, match_type, confidence,
        description e timestamp ISO 8601."""
        service = AlertService(
            config=alert_config,
            detection_store=empty_detection_store,
            email_provider=fake_provider,
        )

        await service.send_alert(
            sample_detection, ["admin@example.com"]
        )

        body = fake_provider.sent_emails[0]["body"]

        # URL do site-alvo
        assert sample_detection.target_url in body
        # Tipo de match
        assert sample_detection.match_type in body
        # Confiança
        assert str(sample_detection.confidence) in body
        # Descrição
        assert sample_detection.description in body
        # Timestamp ISO 8601
        assert "2024-06-15T14:30:00Z" in body

    async def test_email_body_contains_bounding_box_coordinates(
        self,
        alert_config: AlertConfig,
        sample_detection: DetectionResult,
        fake_provider: FakeEmailProvider,
        empty_detection_store: FakeDetectionStore,
    ) -> None:
        """Body do email deve incluir coordenadas do bounding box."""
        service = AlertService(
            config=alert_config,
            detection_store=empty_detection_store,
            email_provider=fake_provider,
        )

        await service.send_alert(
            sample_detection, ["admin@example.com"]
        )

        body = fake_provider.sent_emails[0]["body"]
        bbox = sample_detection.bounding_box

        # Coordenadas formatadas com 1 casa decimal
        assert f"{bbox.x_percent:.1f}%" in body
        assert f"{bbox.y_percent:.1f}%" in body
        assert f"{bbox.width_percent:.1f}%" in body
        assert f"{bbox.height_percent:.1f}%" in body

    async def test_duplicate_detection_is_suppressed(
        self,
        alert_config: AlertConfig,
        sample_detection: DetectionResult,
        fake_provider: FakeEmailProvider,
    ) -> None:
        """Alerta duplicado (mesmo target, match_type e bbox) deve ser
        suprimido."""
        # Detecção anterior com mesmo target, tipo e bbox similar
        previous_detection = DetectionResult(
            target_url=sample_detection.target_url,
            match_type=sample_detection.match_type,
            confidence=80,
            bounding_box=BoundingBox(
                x_percent=11.0,  # Dentro da tolerância de 5%
                y_percent=26.0,
                width_percent=15.5,
                height_percent=8.0,
            ),
            description="Detecção do ciclo anterior",
            detected_at=datetime(
                2024, 6, 14, 14, 30, 0, tzinfo=timezone.utc
            ),
            screenshot_ref_id="screenshot-prev-001",
        )

        store_with_previous = FakeDetectionStore(
            previous_detections=[previous_detection]
        )

        service = AlertService(
            config=alert_config,
            detection_store=store_with_previous,
            email_provider=fake_provider,
        )

        result = await service.send_alert(
            sample_detection, ["admin@example.com"]
        )

        # Alerta suprimido com sucesso (retorna True, sem envio)
        assert result is True
        assert len(fake_provider.sent_emails) == 0

    async def test_different_bbox_is_not_suppressed(
        self,
        alert_config: AlertConfig,
        sample_detection: DetectionResult,
        fake_provider: FakeEmailProvider,
    ) -> None:
        """Detecção com bbox diferente (fora da tolerância) não é
        suprimida."""
        # Detecção anterior com bbox muito diferente
        previous_detection = DetectionResult(
            target_url=sample_detection.target_url,
            match_type=sample_detection.match_type,
            confidence=80,
            bounding_box=BoundingBox(
                x_percent=60.0,  # Muito diferente (fora de 5%)
                y_percent=80.0,
                width_percent=20.0,
                height_percent=12.0,
            ),
            description="Detecção em posição diferente",
            detected_at=datetime(
                2024, 6, 14, 14, 30, 0, tzinfo=timezone.utc
            ),
            screenshot_ref_id="screenshot-prev-002",
        )

        store_with_previous = FakeDetectionStore(
            previous_detections=[previous_detection]
        )

        service = AlertService(
            config=alert_config,
            detection_store=store_with_previous,
            email_provider=fake_provider,
        )

        result = await service.send_alert(
            sample_detection, ["admin@example.com"]
        )

        # Email deve ser enviado (não é duplicata)
        assert result is True
        assert len(fake_provider.sent_emails) == 1

    async def test_text_match_type_email_format(
        self,
        alert_config: AlertConfig,
        fake_provider: FakeEmailProvider,
        empty_detection_store: FakeDetectionStore,
    ) -> None:
        """Detecção do tipo 'text' formata corretamente o email."""
        text_detection = DetectionResult(
            target_url="https://example.org/landing",
            match_type="text",
            confidence=92,
            bounding_box=BoundingBox(
                x_percent=5.0,
                y_percent=50.0,
                width_percent=30.0,
                height_percent=3.0,
            ),
            description="Menção textual da marca no rodapé",
            detected_at=datetime(
                2024, 7, 1, 10, 0, 0, tzinfo=timezone.utc
            ),
            screenshot_ref_id="screenshot-text-001",
        )

        service = AlertService(
            config=alert_config,
            detection_store=empty_detection_store,
            email_provider=fake_provider,
        )

        await service.send_alert(
            text_detection, ["admin@example.com"]
        )

        email = fake_provider.sent_emails[0]
        # Subject contém "Texto" para match_type "text"
        assert "Texto" in email["subject"]
        assert text_detection.target_url in email["subject"]
        # Body contém todos os campos
        assert "text" in email["body"]
        assert "92" in email["body"]
        assert "2024-07-01T10:00:00Z" in email["body"]


# ---------------------------------------------------------------------------
# Testes de integração: SESProvider com boto3 mockado
# ---------------------------------------------------------------------------


class TestSESProviderIntegration:
    """Testes do SESProvider com mock do boto3."""

    async def test_ses_provider_sends_email_via_boto3(
        self, alert_config: AlertConfig
    ) -> None:
        """SESProvider deve chamar boto3 ses.send_email corretamente."""
        with patch("brand_watchdog.alerts.email_providers.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            provider = SESProvider(alert_config)

            await provider.send(
                recipient="user@example.com",
                subject="[Brand Watchdog] Detecção de Logo",
                body="Detalhes do alerta...",
                sender="watchdog@example.com",
            )

            mock_client.send_email.assert_called_once()
            call_kwargs = mock_client.send_email.call_args[1]

            assert call_kwargs["Source"] == "watchdog@example.com"
            assert call_kwargs["Destination"]["ToAddresses"] == [
                "user@example.com"
            ]
            assert (
                call_kwargs["Message"]["Subject"]["Data"]
                == "[Brand Watchdog] Detecção de Logo"
            )
            assert (
                call_kwargs["Message"]["Body"]["Text"]["Data"]
                == "Detalhes do alerta..."
            )

    async def test_ses_provider_uses_configured_region(
        self,
    ) -> None:
        """SESProvider deve criar client boto3 na região configurada."""
        config = AlertConfig(
            provider="ses",
            ses_region="eu-west-1",
            ses_sender="brand@company.com",
        )

        with patch("brand_watchdog.alerts.email_providers.boto3") as mock_boto3:
            mock_boto3.client.return_value = MagicMock()

            SESProvider(config)

            mock_boto3.client.assert_called_once_with(
                "ses", region_name="eu-west-1"
            )

    async def test_ses_provider_propagates_client_error(
        self, alert_config: AlertConfig
    ) -> None:
        """SESProvider deve propagar ClientError do boto3."""
        from botocore.exceptions import ClientError

        with patch("brand_watchdog.alerts.email_providers.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_client.send_email.side_effect = ClientError(
                {"Error": {"Code": "MessageRejected", "Message": "Email rejected"}},
                "SendEmail",
            )
            mock_boto3.client.return_value = mock_client

            provider = SESProvider(alert_config)

            with pytest.raises(ClientError):
                await provider.send(
                    recipient="user@example.com",
                    subject="Test",
                    body="Test body",
                    sender="watchdog@example.com",
                )


# ---------------------------------------------------------------------------
# Testes de integração: SMTPProvider com aiosmtplib mockado
# ---------------------------------------------------------------------------


class TestSMTPProviderIntegration:
    """Testes do SMTPProvider com mock do aiosmtplib."""

    async def test_smtp_provider_sends_email_via_aiosmtplib(
        self,
    ) -> None:
        """SMTPProvider deve chamar aiosmtplib.send com params corretos."""
        config = AlertConfig(
            provider="smtp",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user@example.com",
            smtp_password="secret123",
        )

        with patch("brand_watchdog.alerts.email_providers.aiosmtplib") as mock_smtp:
            mock_smtp.send = AsyncMock()

            provider = SMTPProvider(config)

            await provider.send(
                recipient="dest@example.com",
                subject="[Brand Watchdog] Alerta",
                body="Corpo do alerta",
                sender="watchdog@example.com",
            )

            mock_smtp.send.assert_called_once()
            call_kwargs = mock_smtp.send.call_args[1]

            assert call_kwargs["hostname"] == "smtp.example.com"
            assert call_kwargs["port"] == 587
            assert call_kwargs["username"] == "user@example.com"
            assert call_kwargs["password"] == "secret123"
            assert call_kwargs["start_tls"] is True

    async def test_smtp_provider_constructs_mime_message(
        self,
    ) -> None:
        """SMTPProvider deve construir MIMEMultipart com campos corretos."""
        config = AlertConfig(
            provider="smtp",
            smtp_host="mail.test.com",
            smtp_port=465,
            smtp_username="smtp_user",
            smtp_password="smtp_pass",
        )

        with patch("brand_watchdog.alerts.email_providers.aiosmtplib") as mock_smtp:
            mock_smtp.send = AsyncMock()

            provider = SMTPProvider(config)

            await provider.send(
                recipient="admin@test.com",
                subject="Assunto do Teste",
                body="Corpo do email de teste",
                sender="remetente@test.com",
            )

            # Verifica que o primeiro argumento é o MIMEMultipart
            call_args = mock_smtp.send.call_args
            message = call_args[0][0]

            assert message["From"] == "remetente@test.com"
            assert message["To"] == "admin@test.com"
            assert message["Subject"] == "Assunto do Teste"

    async def test_smtp_provider_propagates_smtp_exception(
        self,
    ) -> None:
        """SMTPProvider deve propagar SMTPException do aiosmtplib."""
        import aiosmtplib

        config = AlertConfig(
            provider="smtp",
            smtp_host="smtp.fail.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
        )

        with patch("brand_watchdog.alerts.email_providers.aiosmtplib") as mock_smtp:
            mock_smtp.send = AsyncMock(
                side_effect=aiosmtplib.SMTPException("Connection refused")
            )
            mock_smtp.SMTPException = aiosmtplib.SMTPException

            provider = SMTPProvider(config)

            with pytest.raises(aiosmtplib.SMTPException):
                await provider.send(
                    recipient="user@example.com",
                    subject="Test",
                    body="Test body",
                    sender="sender@example.com",
                )
