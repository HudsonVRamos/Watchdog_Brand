"""Testes unitários para o AlertService.

Valida:
- Envio de alertas para múltiplos destinatários
- Supressão de alertas duplicados (bounding box com tolerância 5%)
- Formatação do email com todos os campos obrigatórios
- Retry em caso de falha
- Log de falha definitiva
- Seleção de provider (SES vs SMTP) na integração com AlertService
- Retry com contagens variáveis
- Supressão com múltiplas detecções anteriores e sobreposição parcial
- Formatação com URLs longas e caracteres especiais
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.alerts.alert_service import (
    AlertService,
    EmailProvider,
)
from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import BoundingBox, DetectionResult
from brand_watchdog.storage.detection_store import DetectionStore


# --- Fixtures e Helpers ---


class FakeEmailProvider(EmailProvider):
    """Provedor de email fake para testes."""

    def __init__(self, should_fail: bool = False) -> None:
        self.sent_emails: list[dict] = []
        self.should_fail = should_fail
        self.call_count = 0

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        sender: str,
    ) -> None:
        self.call_count += 1
        if self.should_fail:
            raise ConnectionError("Falha simulada no envio")
        self.sent_emails.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "sender": sender,
            }
        )


def _make_detection(
    target_url: str = "https://example.com",
    match_type: str = "logo",
    confidence: int = 85,
    x: float = 10.0,
    y: float = 20.0,
    w: float = 30.0,
    h: float = 15.0,
    description: str = "Logo detectado no cabeçalho",
    detected_at: datetime | None = None,
) -> DetectionResult:
    """Cria um DetectionResult para testes."""
    if detected_at is None:
        detected_at = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
    return DetectionResult(
        target_url=target_url,
        match_type=match_type,
        confidence=confidence,
        bounding_box=BoundingBox(
            x_percent=x,
            y_percent=y,
            width_percent=w,
            height_percent=h,
        ),
        description=description,
        detected_at=detected_at,
        screenshot_ref_id="ref-123",
    )


def _make_config(
    sender: str = "alert@brandwatchdog.com",
    retry_attempts: int = 3,
    retry_interval: int = 0,
) -> AlertConfig:
    """Cria AlertConfig para testes (retry_interval=0 para velocidade)."""
    return AlertConfig(
        ses_sender=sender,
        retry_attempts=retry_attempts,
        retry_interval_seconds=retry_interval,
    )


def _make_detection_store(
    previous_detections: list[DetectionResult] | None = None,
) -> DetectionStore:
    """Cria um mock do DetectionStore."""
    store = AsyncMock(spec=DetectionStore)
    store.get_previous_cycle_detections = AsyncMock(
        return_value=previous_detections or []
    )
    return store


# --- Testes de Envio ---


@pytest.mark.asyncio
async def test_send_alert_success_single_recipient():
    """Envia alerta com sucesso para um único destinatário."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)
    detection = _make_detection()

    result = await service.send_alert(detection, ["user@test.com"])

    assert result is True
    assert len(provider.sent_emails) == 1
    assert provider.sent_emails[0]["recipient"] == "user@test.com"


@pytest.mark.asyncio
async def test_send_alert_success_multiple_recipients():
    """Envia alerta para múltiplos destinatários."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)
    detection = _make_detection()

    recipients = ["a@test.com", "b@test.com", "c@test.com"]
    result = await service.send_alert(detection, recipients)

    assert result is True
    assert len(provider.sent_emails) == 3
    sent_to = {e["recipient"] for e in provider.sent_emails}
    assert sent_to == {"a@test.com", "b@test.com", "c@test.com"}


@pytest.mark.asyncio
async def test_send_alert_returns_false_when_provider_not_configured():
    """Retorna False se o provedor de email não está configurado."""
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, email_provider=None)
    detection = _make_detection()

    result = await service.send_alert(detection, ["user@test.com"])

    assert result is False


# --- Testes de Supressão de Duplicatas ---


@pytest.mark.asyncio
async def test_suppresses_duplicate_same_bbox():
    """Suprime alerta quando detecção idêntica existe no ciclo anterior."""
    prev_detection = _make_detection(
        x=10.0, y=20.0, w=30.0, h=15.0
    )
    store = _make_detection_store(
        previous_detections=[prev_detection]
    )
    provider = FakeEmailProvider()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection(x=10.0, y=20.0, w=30.0, h=15.0)
    result = await service.send_alert(detection, ["user@test.com"])

    assert result is True
    assert len(provider.sent_emails) == 0


@pytest.mark.asyncio
async def test_suppresses_duplicate_within_tolerance():
    """Suprime alerta quando bbox está dentro da tolerância de 5%."""
    prev_detection = _make_detection(
        x=10.0, y=20.0, w=30.0, h=15.0
    )
    store = _make_detection_store(
        previous_detections=[prev_detection]
    )
    provider = FakeEmailProvider()
    config = _make_config()
    service = AlertService(config, store, provider)

    # Diferença de 4.9% em cada coordenada (dentro da tolerância)
    detection = _make_detection(
        x=14.9, y=24.9, w=34.9, h=19.9
    )
    result = await service.send_alert(detection, ["user@test.com"])

    assert result is True
    assert len(provider.sent_emails) == 0


@pytest.mark.asyncio
async def test_does_not_suppress_when_bbox_exceeds_tolerance():
    """Não suprime alerta quando bbox excede tolerância de 5%."""
    prev_detection = _make_detection(
        x=10.0, y=20.0, w=30.0, h=15.0
    )
    store = _make_detection_store(
        previous_detections=[prev_detection]
    )
    provider = FakeEmailProvider()
    config = _make_config()
    service = AlertService(config, store, provider)

    # Diferença de 5.1% em x (fora da tolerância)
    detection = _make_detection(
        x=15.1, y=20.0, w=30.0, h=15.0
    )
    result = await service.send_alert(detection, ["user@test.com"])

    assert result is True
    assert len(provider.sent_emails) == 1


@pytest.mark.asyncio
async def test_does_not_suppress_different_match_type():
    """Não suprime alerta quando match_type é diferente."""
    prev_detection = _make_detection(match_type="logo")
    store = _make_detection_store(
        previous_detections=[prev_detection]
    )
    provider = FakeEmailProvider()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection(match_type="text")
    result = await service.send_alert(detection, ["user@test.com"])

    assert result is True
    assert len(provider.sent_emails) == 1


@pytest.mark.asyncio
async def test_does_not_suppress_when_no_previous_detections():
    """Não suprime alerta quando não há detecções anteriores."""
    store = _make_detection_store(previous_detections=[])
    provider = FakeEmailProvider()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection()
    result = await service.send_alert(detection, ["user@test.com"])

    assert result is True
    assert len(provider.sent_emails) == 1


# --- Testes de Formatação de Email ---


@pytest.mark.asyncio
async def test_email_contains_target_url():
    """Email contém URL do site-alvo."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection(
        target_url="https://suspect-site.com/page"
    )
    await service.send_alert(detection, ["user@test.com"])

    body = provider.sent_emails[0]["body"]
    assert "https://suspect-site.com/page" in body


@pytest.mark.asyncio
async def test_email_contains_match_type():
    """Email contém tipo de match (logo/text)."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection(match_type="logo")
    await service.send_alert(detection, ["user@test.com"])

    body = provider.sent_emails[0]["body"]
    assert "Logo" in body
    assert "logo" in body


@pytest.mark.asyncio
async def test_email_contains_confidence():
    """Email contém nível de confiança (0-100)."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection(confidence=92)
    await service.send_alert(detection, ["user@test.com"])

    body = provider.sent_emails[0]["body"]
    assert "92%" in body


@pytest.mark.asyncio
async def test_email_contains_description():
    """Email contém descrição do match."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection(
        description="Logo encontrado no rodapé da página"
    )
    await service.send_alert(detection, ["user@test.com"])

    body = provider.sent_emails[0]["body"]
    assert "Logo encontrado no rodapé da página" in body


@pytest.mark.asyncio
async def test_email_contains_iso_8601_timestamp():
    """Email contém timestamp em formato ISO 8601."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)

    dt = datetime(2024, 3, 15, 14, 30, 45, tzinfo=timezone.utc)
    detection = _make_detection(detected_at=dt)
    await service.send_alert(detection, ["user@test.com"])

    body = provider.sent_emails[0]["body"]
    assert "2024-03-15T14:30:45Z" in body


@pytest.mark.asyncio
async def test_email_subject_contains_match_type_and_url():
    """Subject do email contém tipo de match e URL."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config()
    service = AlertService(config, store, provider)

    detection = _make_detection(
        target_url="https://test.com",
        match_type="text",
    )
    await service.send_alert(detection, ["user@test.com"])

    subject = provider.sent_emails[0]["subject"]
    assert "Texto" in subject
    assert "https://test.com" in subject
    assert "Brand Watchdog" in subject


# --- Testes de Retry ---


@pytest.mark.asyncio
async def test_retry_on_send_failure():
    """Retenta envio em caso de falha."""
    provider = FakeEmailProvider(should_fail=True)
    store = _make_detection_store()
    config = _make_config(retry_attempts=3, retry_interval=0)
    service = AlertService(config, store, provider)

    detection = _make_detection()
    result = await service.send_alert(detection, ["user@test.com"])

    assert result is False
    # Deve tentar 3 vezes (retry_attempts=3)
    assert provider.call_count == 3


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    """Alerta enviado com sucesso na segunda tentativa."""
    provider = FakeEmailProvider()
    store = _make_detection_store()
    config = _make_config(retry_attempts=3, retry_interval=0)
    service = AlertService(config, store, provider)

    # Falha na primeira, sucesso na segunda
    call_count = {"value": 0}
    original_send = provider.send

    async def flaky_send(**kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise ConnectionError("Falha temporária")
        await original_send(**kwargs)

    provider.send = flaky_send

    detection = _make_detection()
    result = await service.send_alert(detection, ["user@test.com"])

    assert result is True
    assert call_count["value"] == 2


# --- Testes do Bounding Box Overlap ---


class TestBoundingBoxOverlap:
    """Testes para a lógica de comparação de bounding boxes."""

    def _service(self) -> AlertService:
        store = _make_detection_store()
        config = _make_config()
        return AlertService(config, store)

    def test_identical_boxes_overlap(self):
        """Boxes idênticos são considerados duplicatas."""
        service = self._service()
        box = BoundingBox(10.0, 20.0, 30.0, 15.0)
        assert service._bounding_boxes_overlap(box, box) is True

    def test_boxes_at_exact_tolerance_overlap(self):
        """Boxes com diferença exata de 5% são duplicatas."""
        service = self._service()
        box1 = BoundingBox(10.0, 20.0, 30.0, 15.0)
        box2 = BoundingBox(15.0, 25.0, 35.0, 20.0)
        assert service._bounding_boxes_overlap(box1, box2) is True

    def test_boxes_beyond_tolerance_do_not_overlap(self):
        """Boxes com diferença > 5% não são duplicatas."""
        service = self._service()
        box1 = BoundingBox(10.0, 20.0, 30.0, 15.0)
        box2 = BoundingBox(15.1, 20.0, 30.0, 15.0)
        assert service._bounding_boxes_overlap(box1, box2) is False

    def test_y_exceeds_tolerance(self):
        """Diferença em y_percent excede tolerância."""
        service = self._service()
        box1 = BoundingBox(10.0, 20.0, 30.0, 15.0)
        box2 = BoundingBox(10.0, 25.1, 30.0, 15.0)
        assert service._bounding_boxes_overlap(box1, box2) is False

    def test_width_exceeds_tolerance(self):
        """Diferença em width_percent excede tolerância."""
        service = self._service()
        box1 = BoundingBox(10.0, 20.0, 30.0, 15.0)
        box2 = BoundingBox(10.0, 20.0, 35.1, 15.0)
        assert service._bounding_boxes_overlap(box1, box2) is False

    def test_height_exceeds_tolerance(self):
        """Diferença em height_percent excede tolerância."""
        service = self._service()
        box1 = BoundingBox(10.0, 20.0, 30.0, 15.0)
        box2 = BoundingBox(10.0, 20.0, 30.0, 20.1)
        assert service._bounding_boxes_overlap(box1, box2) is False


# --- Testes de Seleção de Provider (Req 6.3) ---


class TestProviderSelection:
    """Testes para integração AlertService com diferentes providers."""

    @patch("brand_watchdog.alerts.email_providers.boto3")
    @pytest.mark.asyncio
    async def test_alert_service_with_ses_provider(
        self, mock_boto3
    ):
        """AlertService envia via SESProvider quando configurado."""
        from brand_watchdog.alerts.email_providers import (
            create_email_provider,
        )

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        config = _make_config(sender="ses@brand.com")
        config.provider = "ses"
        config.ses_region = "us-east-1"

        provider = create_email_provider(config)
        store = _make_detection_store()
        service = AlertService(config, store, provider)

        detection = _make_detection()
        result = await service.send_alert(
            detection, ["user@test.com"]
        )

        assert result is True
        mock_client.send_email.assert_called_once()
        call_kwargs = mock_client.send_email.call_args[1]
        assert call_kwargs["Source"] == "ses@brand.com"
        assert (
            call_kwargs["Destination"]["ToAddresses"]
            == ["user@test.com"]
        )

    @patch("brand_watchdog.alerts.email_providers.aiosmtplib")
    @pytest.mark.asyncio
    async def test_alert_service_with_smtp_provider(
        self, mock_aiosmtplib
    ):
        """AlertService envia via SMTPProvider quando configurado."""
        from brand_watchdog.alerts.email_providers import (
            create_email_provider,
        )

        mock_aiosmtplib.send = AsyncMock()
        mock_aiosmtplib.SMTPException = Exception

        config = _make_config(sender="smtp@brand.com")
        config.provider = "smtp"
        config.smtp_host = "mail.brand.com"
        config.smtp_port = 587
        config.smtp_username = "user"
        config.smtp_password = "secret"

        provider = create_email_provider(config)
        store = _make_detection_store()
        service = AlertService(config, store, provider)

        detection = _make_detection()
        result = await service.send_alert(
            detection, ["dest@test.com"]
        )

        assert result is True
        mock_aiosmtplib.send.assert_called_once()
        call_kwargs = mock_aiosmtplib.send.call_args[1]
        assert call_kwargs["hostname"] == "mail.brand.com"
        assert call_kwargs["port"] == 587


# --- Testes de Retry com Contagens Variáveis (Req 6.4) ---


class TestRetryBehavior:
    """Testes avançados de retry com diferentes configurações."""

    @pytest.mark.asyncio
    async def test_retry_attempts_1_no_retries(self):
        """Com retry_attempts=1, tenta apenas uma vez."""
        provider = FakeEmailProvider(should_fail=True)
        store = _make_detection_store()
        config = _make_config(retry_attempts=1, retry_interval=0)
        service = AlertService(config, store, provider)

        detection = _make_detection()
        result = await service.send_alert(
            detection, ["user@test.com"]
        )

        assert result is False
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_attempts_5_all_fail(self):
        """Com retry_attempts=5, tenta exatamente 5 vezes."""
        provider = FakeEmailProvider(should_fail=True)
        store = _make_detection_store()
        config = _make_config(retry_attempts=5, retry_interval=0)
        service = AlertService(config, store, provider)

        detection = _make_detection()
        result = await service.send_alert(
            detection, ["user@test.com"]
        )

        assert result is False
        assert provider.call_count == 5

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_last_attempt(self):
        """Envio com sucesso na última tentativa (3ª de 3)."""
        provider = FakeEmailProvider()
        store = _make_detection_store()
        config = _make_config(retry_attempts=3, retry_interval=0)
        service = AlertService(config, store, provider)

        attempts = {"count": 0}
        original_send = provider.send

        async def fail_until_last(**kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ConnectionError("Falha temporária")
            await original_send(**kwargs)

        provider.send = fail_until_last

        detection = _make_detection()
        result = await service.send_alert(
            detection, ["user@test.com"]
        )

        assert result is True
        assert attempts["count"] == 3

    @pytest.mark.asyncio
    async def test_retry_per_recipient_independent(self):
        """Falha em um destinatário não impede envio a outros."""
        store = _make_detection_store()
        config = _make_config(retry_attempts=2, retry_interval=0)

        send_log: list[str] = []
        fail_recipient = "fail@test.com"

        class SelectiveFailProvider(EmailProvider):
            async def send(
                self,
                recipient: str,
                subject: str,
                body: str,
                sender: str,
            ) -> None:
                send_log.append(recipient)
                if recipient == fail_recipient:
                    raise ConnectionError("Simulada")

        provider = SelectiveFailProvider()
        service = AlertService(config, store, provider)

        detection = _make_detection()
        result = await service.send_alert(
            detection,
            [fail_recipient, "ok@test.com"],
        )

        # False porque fail_recipient falhou
        assert result is False
        # ok@test.com deve ter sido enviado com sucesso
        assert "ok@test.com" in send_log


# --- Testes de Supressão Edge Cases (Req 6.7) ---


class TestSuppressionEdgeCases:
    """Testes de supressão com cenários complexos."""

    @pytest.mark.asyncio
    async def test_suppresses_when_multiple_previous_and_one_matches(
        self,
    ):
        """Suprime quando há múltiplas detecções anteriores e uma delas
        coincide com a detecção atual."""
        prev_detections = [
            _make_detection(
                match_type="logo", x=50.0, y=50.0, w=20.0, h=10.0
            ),
            _make_detection(
                match_type="text", x=5.0, y=80.0, w=40.0, h=5.0
            ),
            _make_detection(
                match_type="logo", x=10.0, y=20.0, w=30.0, h=15.0
            ),
        ]
        store = _make_detection_store(
            previous_detections=prev_detections
        )
        provider = FakeEmailProvider()
        config = _make_config()
        service = AlertService(config, store, provider)

        # Coincide com a 3ª detecção anterior (logo, mesma bbox)
        detection = _make_detection(
            match_type="logo", x=10.0, y=20.0, w=30.0, h=15.0
        )
        result = await service.send_alert(
            detection, ["user@test.com"]
        )

        assert result is True
        assert len(provider.sent_emails) == 0

    @pytest.mark.asyncio
    async def test_does_not_suppress_when_none_of_multiple_match(
        self,
    ):
        """Não suprime quando nenhuma detecção anterior coincide."""
        prev_detections = [
            _make_detection(
                match_type="logo", x=50.0, y=50.0, w=20.0, h=10.0
            ),
            _make_detection(
                match_type="text", x=5.0, y=80.0, w=40.0, h=5.0
            ),
        ]
        store = _make_detection_store(
            previous_detections=prev_detections
        )
        provider = FakeEmailProvider()
        config = _make_config()
        service = AlertService(config, store, provider)

        # Logo em posição diferente de todas as anteriores
        detection = _make_detection(
            match_type="logo", x=70.0, y=10.0, w=15.0, h=8.0
        )
        result = await service.send_alert(
            detection, ["user@test.com"]
        )

        assert result is True
        assert len(provider.sent_emails) == 1

    @pytest.mark.asyncio
    async def test_suppresses_partial_overlap_same_type_only(self):
        """Suprime somente se match_type coincide, mesmo com bbox
        similar a detecção de tipo diferente."""
        prev_detections = [
            # Logo em posição X
            _make_detection(
                match_type="logo", x=10.0, y=20.0, w=30.0, h=15.0
            ),
            # Texto na mesma posição X
            _make_detection(
                match_type="text", x=10.0, y=20.0, w=30.0, h=15.0
            ),
        ]
        store = _make_detection_store(
            previous_detections=prev_detections
        )
        provider = FakeEmailProvider()
        config = _make_config()
        service = AlertService(config, store, provider)

        # Detecção tipo "logo" na mesma posição → suprime
        detection_logo = _make_detection(
            match_type="logo", x=10.0, y=20.0, w=30.0, h=15.0
        )
        result = await service.send_alert(
            detection_logo, ["user@test.com"]
        )
        assert result is True
        assert len(provider.sent_emails) == 0

    @pytest.mark.asyncio
    async def test_does_not_suppress_different_target_url(self):
        """Não suprime quando target_url é diferente (store filtra por URL)."""
        # Detecção anterior em outro URL (store retorna vazio para URL
        # diferente)
        store = _make_detection_store(previous_detections=[])
        provider = FakeEmailProvider()
        config = _make_config()
        service = AlertService(config, store, provider)

        detection = _make_detection(
            target_url="https://novo-site.com",
            x=10.0, y=20.0, w=30.0, h=15.0,
        )
        result = await service.send_alert(
            detection, ["user@test.com"]
        )

        assert result is True
        assert len(provider.sent_emails) == 1


# --- Testes de Formatação com Edge Cases (Req 6.1) ---


class TestEmailFormatEdgeCases:
    """Testes de formatação de email com entradas extremas."""

    @pytest.mark.asyncio
    async def test_email_with_very_long_url(self):
        """Email formata corretamente URL com 2000+ caracteres."""
        long_path = "/page/" + "a" * 1980
        long_url = f"https://example.com{long_path}"
        provider = FakeEmailProvider()
        store = _make_detection_store()
        config = _make_config()
        service = AlertService(config, store, provider)

        detection = _make_detection(target_url=long_url)
        await service.send_alert(detection, ["user@test.com"])

        body = provider.sent_emails[0]["body"]
        subject = provider.sent_emails[0]["subject"]
        assert long_url in body
        assert long_url in subject

    @pytest.mark.asyncio
    async def test_email_with_special_chars_in_description(self):
        """Email preserva caracteres especiais na descrição."""
        special_desc = (
            "Logo <img> encontrado em "
            '"seção & rodapé" — com acentuação: ção, ü, ñ'
        )
        provider = FakeEmailProvider()
        store = _make_detection_store()
        config = _make_config()
        service = AlertService(config, store, provider)

        detection = _make_detection(description=special_desc)
        await service.send_alert(detection, ["user@test.com"])

        body = provider.sent_emails[0]["body"]
        assert special_desc in body

    @pytest.mark.asyncio
    async def test_email_with_unicode_in_url(self):
        """Email formata URL com caracteres Unicode encoded."""
        unicode_url = (
            "https://example.com/p%C3%A1gina/"
            "%E4%B8%AD%E6%96%87"
        )
        provider = FakeEmailProvider()
        store = _make_detection_store()
        config = _make_config()
        service = AlertService(config, store, provider)

        detection = _make_detection(target_url=unicode_url)
        await service.send_alert(detection, ["user@test.com"])

        body = provider.sent_emails[0]["body"]
        assert unicode_url in body

    @pytest.mark.asyncio
    async def test_email_confidence_boundary_values(self):
        """Email formata corretamente confiança 0% e 100%."""
        provider = FakeEmailProvider()
        store = _make_detection_store()
        config = _make_config()
        service = AlertService(config, store, provider)

        # Confiança 0%
        detection_low = _make_detection(confidence=0)
        await service.send_alert(
            detection_low, ["user@test.com"]
        )
        assert "0%" in provider.sent_emails[0]["body"]

        # Confiança 100%
        detection_high = _make_detection(confidence=100)
        await service.send_alert(
            detection_high, ["user@test.com"]
        )
        assert "100%" in provider.sent_emails[1]["body"]
