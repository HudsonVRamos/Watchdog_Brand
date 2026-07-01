"""Serviço de envio de alertas de detecção de marca.

Gerencia o envio de notificações por email quando uso não autorizado
de marca é detectado, incluindo:
- Formatação de email com detalhes da detecção
- Supressão de alertas duplicados (mesma detecção em ciclos consecutivos)
- Retry configurável com log de falhas
- Suporte a múltiplos destinatários
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import timezone

from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import BoundingBox, DetectionResult
from brand_watchdog.storage.detection_store import DetectionStore

logger = logging.getLogger(__name__)

# Tolerância para comparação de bounding boxes (5% em cada coordenada)
_BBOX_TOLERANCE = 5.0


class EmailProvider(ABC):
    """Interface abstrata para provedores de email.

    Implementações concretas (SES, SMTP) devem ser fornecidas
    na task 11.2.
    """

    @abstractmethod
    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        sender: str,
    ) -> None:
        """Envia um email para o destinatário.

        Args:
            recipient: Endereço de email do destinatário.
            subject: Assunto do email.
            body: Corpo do email.
            sender: Endereço do remetente.

        Raises:
            Exception: Se o envio falhar.
        """


class AlertService:
    """Serviço de alertas por email para detecções de marca.

    Responsabilidades:
        - Enviar alertas individuais para cada destinatário
        - Suprimir alertas duplicados (mesma detecção em ciclos consecutivos)
        - Formatar emails com detalhes completos da detecção
        - Retry configurável com log de falhas

    Args:
        config: Configuração de alertas (provider, retries, etc.).
        detection_store: Store para consulta de detecções anteriores.
        email_provider: Provedor de email (SES ou SMTP).
    """

    def __init__(
        self,
        config: AlertConfig,
        detection_store: DetectionStore,
        email_provider: EmailProvider | None = None,
    ) -> None:
        self._config = config
        self._detection_store = detection_store
        self._email_provider = email_provider

    async def send_alert(
        self,
        detection: DetectionResult,
        recipients: list[str],
    ) -> bool:
        """Envia alerta individual para cada destinatário.

        Verifica supressão de duplicatas antes do envio. Se a detecção
        for duplicata do ciclo anterior, o alerta é suprimido e o método
        retorna True (sucesso, sem envio necessário).

        Args:
            detection: Resultado de detecção que gerou o alerta.
            recipients: Lista de endereços de email dos destinatários.

        Returns:
            True se o alerta foi enviado (ou suprimido) com sucesso.
            False se houve falha no envio para algum destinatário.
        """
        # Verifica supressão de duplicatas
        if await self._should_suppress(detection):
            logger.info(
                "Alerta suprimido (duplicata): target=%s, tipo=%s",
                detection.target_url,
                detection.match_type,
            )
            return True

        # Formata email
        subject, body = self._format_alert_email(detection)

        # Envia para cada destinatário
        all_success = True
        for recipient in recipients:
            success = await self._send_with_retry(
                recipient, subject, body, detection
            )
            if not success:
                all_success = False

        return all_success

    async def _should_suppress(
        self, detection: DetectionResult
    ) -> bool:
        """Verifica se o alerta deve ser suprimido (duplicata consecutiva).

        Duplicata = mesmo target_url + mesmo match_type + bounding_box
        com sobreposição dentro da tolerância de 5% em cada coordenada.

        Args:
            detection: Detecção atual a ser verificada.

        Returns:
            True se o alerta deve ser suprimido.
        """
        previous_detections = (
            await self._detection_store.get_previous_cycle_detections(
                target_url=detection.target_url
            )
        )

        for prev in previous_detections:
            if (
                prev.match_type == detection.match_type
                and self._bounding_boxes_overlap(
                    prev.bounding_box, detection.bounding_box
                )
            ):
                return True

        return False

    def _bounding_boxes_overlap(
        self, box1: BoundingBox, box2: BoundingBox
    ) -> bool:
        """Verifica se dois bounding boxes são suficientemente similares.

        Usa tolerância de 5% em cada coordenada (x, y, width, height).
        Se a diferença absoluta em qualquer coordenada exceder 5%,
        os boxes são considerados diferentes.

        Args:
            box1: Primeiro bounding box.
            box2: Segundo bounding box.

        Returns:
            True se os boxes são similares dentro da tolerância.
        """
        return (
            abs(box1.x_percent - box2.x_percent) <= _BBOX_TOLERANCE
            and abs(box1.y_percent - box2.y_percent)
            <= _BBOX_TOLERANCE
            and abs(box1.width_percent - box2.width_percent)
            <= _BBOX_TOLERANCE
            and abs(box1.height_percent - box2.height_percent)
            <= _BBOX_TOLERANCE
        )

    def _format_alert_email(
        self, detection: DetectionResult
    ) -> tuple[str, str]:
        """Formata subject e body do email de alerta.

        O email inclui:
        - URL do site-alvo
        - Tipo de match (logo ou text)
        - Nível de confiança (0-100)
        - Descrição da localização do match
        - Timestamp em formato ISO 8601

        Args:
            detection: Detecção para formatar.

        Returns:
            Tupla (subject, body) do email.
        """
        # Formata tipo de match para exibição
        match_type_display = (
            "Logo" if detection.match_type == "logo" else "Texto"
        )

        # Timestamp ISO 8601 com timezone UTC
        timestamp_iso = detection.detected_at.astimezone(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        subject = (
            f"[Brand Watchdog] Detecção de {match_type_display} "
            f"- {detection.target_url}"
        )

        body = (
            f"Brand Watchdog - Alerta de Detecção de Marca\n"
            f"{'=' * 50}\n\n"
            f"Site Alvo: {detection.target_url}\n"
            f"Tipo de Match: {match_type_display} "
            f"({detection.match_type})\n"
            f"Confiança: {detection.confidence}%\n"
            f"Descrição: {detection.description}\n"
            f"Timestamp: {timestamp_iso}\n\n"
            f"Localização na Página:\n"
            f"  X: {detection.bounding_box.x_percent:.1f}%\n"
            f"  Y: {detection.bounding_box.y_percent:.1f}%\n"
            f"  Largura: "
            f"{detection.bounding_box.width_percent:.1f}%\n"
            f"  Altura: "
            f"{detection.bounding_box.height_percent:.1f}%\n\n"
            f"---\n"
            f"Este é um alerta automático do Brand Watchdog.\n"
        )

        return subject, body

    async def _send_with_retry(
        self,
        recipient: str,
        subject: str,
        body: str,
        detection: DetectionResult,
    ) -> bool:
        """Envia email com retry configurável.

        Tenta enviar até retry_attempts vezes com intervalo
        de retry_interval_seconds entre tentativas.

        Args:
            recipient: Endereço do destinatário.
            subject: Assunto do email.
            body: Corpo do email.
            detection: Detecção associada (para log).

        Returns:
            True se o envio foi bem-sucedido.
        """
        if self._email_provider is None:
            logger.error(
                "Provedor de email não configurado. "
                "Não é possível enviar alerta para %s",
                recipient,
            )
            return False

        sender = self._config.ses_sender
        max_attempts = self._config.retry_attempts
        retry_interval = self._config.retry_interval_seconds

        for attempt in range(1, max_attempts + 1):
            try:
                await self._email_provider.send(
                    recipient=recipient,
                    subject=subject,
                    body=body,
                    sender=sender,
                )
                logger.info(
                    "Alerta enviado com sucesso: "
                    "destinatário=%s, target=%s",
                    recipient,
                    detection.target_url,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "Falha ao enviar alerta (tentativa %d/%d): "
                    "destinatário=%s, target=%s, erro=%s",
                    attempt,
                    max_attempts,
                    recipient,
                    detection.target_url,
                    str(exc),
                )
                if attempt < max_attempts:
                    await asyncio.sleep(retry_interval)

        # Todas as tentativas falharam
        logger.error(
            "Falha definitiva ao enviar alerta após %d tentativas: "
            "destinatário=%s, target=%s",
            max_attempts,
            recipient,
            detection.target_url,
        )
        return False
