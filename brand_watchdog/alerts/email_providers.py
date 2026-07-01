"""Provedores de email: AWS SES e SMTP.

Implementações concretas de EmailProvider para envio de alertas:
- SESProvider: envio via AWS SES (boto3)
- SMTPProvider: envio via SMTP assíncrono (aiosmtplib)

A lógica de retry (3 tentativas, intervalo 30s) é gerenciada
pelo AlertService._send_with_retry(), então os providers apenas
levantam exceções em caso de falha.
"""

from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
import boto3
from botocore.exceptions import BotoCoreError, ClientError

from brand_watchdog.alerts.alert_service import EmailProvider
from brand_watchdog.config import AlertConfig

logger = logging.getLogger(__name__)


class SESProvider(EmailProvider):
    """Provedor de email via AWS SES.

    Utiliza boto3 para enviar emails através do serviço
    Amazon Simple Email Service na região configurada.

    Args:
        config: Configuração de alertas com ses_region.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._client = boto3.client(
            "ses",
            region_name=config.ses_region,
        )

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        sender: str,
    ) -> None:
        """Envia email via AWS SES.

        Args:
            recipient: Endereço de email do destinatário.
            subject: Assunto do email.
            body: Corpo do email em texto plano.
            sender: Endereço do remetente.

        Raises:
            ClientError: Se o SES rejeitar a requisição.
            BotoCoreError: Se houver erro de conexão com AWS.
        """
        try:
            self._client.send_email(
                Source=sender,
                Destination={
                    "ToAddresses": [recipient],
                },
                Message={
                    "Subject": {
                        "Data": subject,
                        "Charset": "UTF-8",
                    },
                    "Body": {
                        "Text": {
                            "Data": body,
                            "Charset": "UTF-8",
                        },
                    },
                },
            )
            logger.debug(
                "Email enviado via SES: destinatário=%s",
                recipient,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Falha ao enviar email via SES: "
                "destinatário=%s, erro=%s",
                recipient,
                str(exc),
            )
            raise


class SMTPProvider(EmailProvider):
    """Provedor de email via SMTP assíncrono.

    Utiliza aiosmtplib para conexão SMTP com STARTTLS,
    autenticação por usuário/senha conforme configuração.

    Args:
        config: Configuração de alertas com smtp_host, smtp_port,
                smtp_username e smtp_password.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        sender: str,
    ) -> None:
        """Envia email via SMTP com STARTTLS.

        Args:
            recipient: Endereço de email do destinatário.
            subject: Assunto do email.
            body: Corpo do email em texto plano.
            sender: Endereço do remetente.

        Raises:
            aiosmtplib.SMTPException: Se houver falha na conexão
                ou envio SMTP.
        """
        message = MIMEMultipart()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain", "utf-8"))

        try:
            await aiosmtplib.send(
                message,
                hostname=self._config.smtp_host,
                port=self._config.smtp_port,
                username=self._config.smtp_username,
                password=self._config.smtp_password,
                start_tls=True,
            )
            logger.debug(
                "Email enviado via SMTP: destinatário=%s",
                recipient,
            )
        except aiosmtplib.SMTPException as exc:
            logger.error(
                "Falha ao enviar email via SMTP: "
                "destinatário=%s, erro=%s",
                recipient,
                str(exc),
            )
            raise


def create_email_provider(config: AlertConfig) -> EmailProvider:
    """Factory para criação de provedor de email.

    Seleciona o provedor com base em config.provider:
    - "ses": retorna SESProvider
    - "smtp": retorna SMTPProvider

    Args:
        config: Configuração de alertas.

    Returns:
        Instância do provedor de email configurado.

    Raises:
        ValueError: Se config.provider não for "ses" nem "smtp".
    """
    if config.provider == "ses":
        return SESProvider(config)
    if config.provider == "smtp":
        return SMTPProvider(config)
    raise ValueError(
        f"Provedor de email desconhecido: '{config.provider}'. "
        f"Use 'ses' ou 'smtp'."
    )
