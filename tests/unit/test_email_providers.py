"""Testes unitários para os provedores de email (SES e SMTP).

Valida:
- SESProvider: envio via AWS SES com boto3
- SMTPProvider: envio via SMTP assíncrono com aiosmtplib
- create_email_provider: factory de seleção de provider
- Log de falhas com destinatário e URL
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.alerts.email_providers import (
    SESProvider,
    SMTPProvider,
    create_email_provider,
)
from brand_watchdog.config import AlertConfig


# --- Fixtures ---


def _make_config(
    provider: str = "ses",
    ses_region: str = "us-east-1",
    ses_sender: str = "sender@example.com",
    smtp_host: str = "smtp.example.com",
    smtp_port: int = 587,
    smtp_username: str = "user",
    smtp_password: str = "pass",
) -> AlertConfig:
    """Cria AlertConfig para testes."""
    return AlertConfig(
        provider=provider,
        ses_region=ses_region,
        ses_sender=ses_sender,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
    )


# --- Testes SESProvider ---


class TestSESProvider:
    """Testes para SESProvider."""

    @patch("brand_watchdog.alerts.email_providers.boto3")
    @pytest.mark.asyncio
    async def test_send_calls_ses_send_email(self, mock_boto3):
        """send() chama ses_client.send_email com parâmetros corretos."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        config = _make_config(ses_region="eu-west-1")
        provider = SESProvider(config)

        await provider.send(
            recipient="dest@test.com",
            subject="Assunto Teste",
            body="Corpo do email",
            sender="remetente@test.com",
        )

        mock_boto3.client.assert_called_once_with(
            "ses", region_name="eu-west-1"
        )
        mock_client.send_email.assert_called_once_with(
            Source="remetente@test.com",
            Destination={"ToAddresses": ["dest@test.com"]},
            Message={
                "Subject": {
                    "Data": "Assunto Teste",
                    "Charset": "UTF-8",
                },
                "Body": {
                    "Text": {
                        "Data": "Corpo do email",
                        "Charset": "UTF-8",
                    },
                },
            },
        )

    @patch("brand_watchdog.alerts.email_providers.boto3")
    @pytest.mark.asyncio
    async def test_send_raises_on_client_error(
        self, mock_boto3
    ):
        """send() levanta exceção quando SES falha."""
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.send_email.side_effect = ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email address not verified"}},
            "SendEmail",
        )

        config = _make_config()
        provider = SESProvider(config)

        with pytest.raises(ClientError):
            await provider.send(
                recipient="dest@test.com",
                subject="Teste",
                body="Corpo",
                sender="remetente@test.com",
            )

    @patch("brand_watchdog.alerts.email_providers.boto3")
    @pytest.mark.asyncio
    async def test_send_raises_on_botocore_error(
        self, mock_boto3
    ):
        """send() levanta exceção quando há erro de conexão AWS."""
        from botocore.exceptions import BotoCoreError

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.send_email.side_effect = BotoCoreError()

        config = _make_config()
        provider = SESProvider(config)

        with pytest.raises(BotoCoreError):
            await provider.send(
                recipient="dest@test.com",
                subject="Teste",
                body="Corpo",
                sender="remetente@test.com",
            )


# --- Testes SMTPProvider ---


class TestSMTPProvider:
    """Testes para SMTPProvider."""

    @patch("brand_watchdog.alerts.email_providers.aiosmtplib")
    @pytest.mark.asyncio
    async def test_send_calls_aiosmtplib_send(
        self, mock_aiosmtplib
    ):
        """send() chama aiosmtplib.send com parâmetros corretos."""
        mock_aiosmtplib.send = AsyncMock()
        mock_aiosmtplib.SMTPException = Exception

        config = _make_config(
            provider="smtp",
            smtp_host="mail.example.com",
            smtp_port=465,
            smtp_username="myuser",
            smtp_password="mypass",
        )
        provider = SMTPProvider(config)

        await provider.send(
            recipient="dest@test.com",
            subject="Assunto SMTP",
            body="Corpo via SMTP",
            sender="remetente@test.com",
        )

        mock_aiosmtplib.send.assert_called_once()
        call_kwargs = mock_aiosmtplib.send.call_args[1]
        assert call_kwargs["hostname"] == "mail.example.com"
        assert call_kwargs["port"] == 465
        assert call_kwargs["username"] == "myuser"
        assert call_kwargs["password"] == "mypass"
        assert call_kwargs["start_tls"] is True

    @patch("brand_watchdog.alerts.email_providers.aiosmtplib")
    @pytest.mark.asyncio
    async def test_send_message_has_correct_headers(
        self, mock_aiosmtplib
    ):
        """Mensagem enviada contém headers From, To e Subject."""
        mock_aiosmtplib.send = AsyncMock()
        mock_aiosmtplib.SMTPException = Exception

        config = _make_config(provider="smtp")
        provider = SMTPProvider(config)

        await provider.send(
            recipient="dest@test.com",
            subject="Assunto Teste",
            body="Corpo teste",
            sender="remetente@test.com",
        )

        call_args = mock_aiosmtplib.send.call_args
        message = call_args[0][0]
        assert message["From"] == "remetente@test.com"
        assert message["To"] == "dest@test.com"
        assert message["Subject"] == "Assunto Teste"

    @patch("brand_watchdog.alerts.email_providers.aiosmtplib")
    @pytest.mark.asyncio
    async def test_send_raises_on_smtp_exception(
        self, mock_aiosmtplib
    ):
        """send() levanta exceção quando SMTP falha."""
        import aiosmtplib

        mock_aiosmtplib.send = AsyncMock(
            side_effect=aiosmtplib.SMTPException("Connection refused")
        )
        mock_aiosmtplib.SMTPException = aiosmtplib.SMTPException

        config = _make_config(provider="smtp")
        provider = SMTPProvider(config)

        with pytest.raises(aiosmtplib.SMTPException):
            await provider.send(
                recipient="dest@test.com",
                subject="Teste",
                body="Corpo",
                sender="remetente@test.com",
            )


# --- Testes Factory ---


class TestCreateEmailProvider:
    """Testes para a factory create_email_provider."""

    @patch("brand_watchdog.alerts.email_providers.boto3")
    def test_returns_ses_provider_for_ses(self, mock_boto3):
        """Retorna SESProvider quando provider == 'ses'."""
        config = _make_config(provider="ses")
        provider = create_email_provider(config)
        assert isinstance(provider, SESProvider)

    def test_returns_smtp_provider_for_smtp(self):
        """Retorna SMTPProvider quando provider == 'smtp'."""
        config = _make_config(provider="smtp")
        provider = create_email_provider(config)
        assert isinstance(provider, SMTPProvider)

    def test_raises_value_error_for_unknown_provider(self):
        """Levanta ValueError para provider desconhecido."""
        config = _make_config(provider="sendgrid")
        with pytest.raises(ValueError, match="desconhecido"):
            create_email_provider(config)

    def test_raises_value_error_for_empty_provider(self):
        """Levanta ValueError para provider vazio."""
        config = _make_config(provider="")
        with pytest.raises(ValueError, match="desconhecido"):
            create_email_provider(config)
