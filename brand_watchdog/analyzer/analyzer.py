"""Analisador de screenshots para detecção de marca via AWS Bedrock.

Coordena a construção de prompts multimodais, invocação do modelo
e parsing de resultados em DetectionResult objects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brand_watchdog.analyzer.bedrock_client import BedrockClient
from brand_watchdog.config import AnalyzerConfig
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    BrandAsset,
    DetectionResult,
)

logger = logging.getLogger(__name__)

# Confidence mínima para considerar como match confirmado (Req 4.5)
CONFIRMED_MATCH_THRESHOLD = 60


class AnalysisIncompleteError(Exception):
    """Erro indicando que a análise não pôde ser concluída."""

    pass


class Analyzer:
    """Analisador de screenshots para detecção de uso de marca.

    Utiliza AWS Bedrock (Claude) para análise multimodal de imagens,
    comparando screenshots capturados contra ativos de marca registrados.

    Attributes:
        _config: Configuração do analisador (threshold, timeouts, etc.)
        _bedrock_client: Cliente para invocação do modelo Bedrock.
    """

    def __init__(
        self,
        config: AnalyzerConfig,
        bedrock_client: BedrockClient | None = None,
    ) -> None:
        """Inicializa o Analyzer.

        Args:
            config: Configuração do analisador.
            bedrock_client: Cliente Bedrock (criado automaticamente se None).
        """
        self._config = config
        self._bedrock_client = bedrock_client or BedrockClient(config)

    async def analyze(
        self,
        screenshot_path: Path,
        brand_assets: list[BrandAsset],
        target_url: str = "",
        screenshot_ref_id: str = "",
    ) -> list[DetectionResult]:
        """Analisa screenshot contra ativos de marca via Bedrock.

        Lê a imagem do disco, constrói prompt multimodal com a lista
        de logos e textos a detectar, invoca o modelo e parseia os
        resultados filtrando por confidence threshold.

        Args:
            screenshot_path: Caminho para o arquivo PNG do screenshot.
            brand_assets: Lista de ativos de marca para detecção.
            target_url: URL do site-alvo (incluída nos resultados).
            screenshot_ref_id: ID de referência do screenshot.

        Returns:
            Lista de DetectionResult com confidence >= threshold.

        Raises:
            AnalysisIncompleteError: Se todas as tentativas falharem.
        """
        if not brand_assets:
            logger.warning(
                "Nenhum ativo de marca fornecido para análise de %s",
                target_url,
            )
            return []

        # Lê a imagem do disco
        try:
            image_bytes = screenshot_path.read_bytes()
        except (OSError, IOError) as exc:
            logger.error(
                "Falha ao ler screenshot %s: %s",
                screenshot_path,
                exc,
            )
            raise AnalysisIncompleteError(
                f"Não foi possível ler o screenshot: {exc}"
            ) from exc

        # Constrói prompt com os ativos de marca
        prompt = self._build_analysis_prompt(brand_assets)

        # Invoca o modelo Bedrock (retry gerenciado pelo BedrockClient)
        try:
            response = await self._bedrock_client.invoke_model(
                image_bytes, prompt
            )
        except Exception as exc:
            logger.error(
                "Falha na análise do screenshot %s após retries: %s",
                screenshot_path,
                exc,
            )
            raise AnalysisIncompleteError(
                f"Análise incompleta para {target_url}: "
                f"falha após tentativas de retry - {exc}"
            ) from exc

        # Parseia a resposta em DetectionResult objects
        detections = self._parse_detection_response(
            response=response,
            target_url=target_url,
            screenshot_ref_id=screenshot_ref_id,
        )

        # Filtra por confidence threshold (>= 60 como confirmado, Req 4.5)
        confirmed = [
            d for d in detections
            if d.confidence >= CONFIRMED_MATCH_THRESHOLD
        ]

        logger.info(
            "Análise concluída para %s: %d detecções totais, "
            "%d confirmadas (confidence >= %d)",
            target_url,
            len(detections),
            len(confirmed),
            CONFIRMED_MATCH_THRESHOLD,
        )

        return confirmed

    def _build_analysis_prompt(
        self, brand_assets: list[BrandAsset]
    ) -> str:
        """Constrói prompt estruturado para análise multimodal.

        O prompt instrui o modelo a:
        1. Analisar a imagem procurando logos/textos específicos
        2. Retornar resultados em JSON estruturado
        3. Incluir bounding boxes como percentuais da imagem

        Args:
            brand_assets: Lista de ativos de marca para detecção.

        Returns:
            String com o prompt formatado para o modelo.
        """
        logo_descriptions: list[str] = []
        text_identifiers: list[str] = []

        for asset in brand_assets:
            if asset.asset_type == "logo":
                logo_descriptions.append(
                    asset.original_filename or "logo sem nome"
                )
            else:
                if asset.text_value:
                    text_identifiers.append(asset.text_value)

        # Monta seções do prompt
        logos_section = (
            f"LOGOS para detectar: {', '.join(logo_descriptions)}"
            if logo_descriptions
            else "LOGOS para detectar: (nenhum)"
        )
        texts_section = (
            f"TEXTOS para detectar: {', '.join(text_identifiers)}"
            if text_identifiers
            else "TEXTOS para detectar: (nenhum)"
        )

        prompt = (
            "Analise esta screenshot de website procurando por "
            "uso de marca.\n\n"
            f"{logos_section}\n"
            f"{texts_section}\n\n"
            "Instruções:\n"
            "- Identifique qualquer ocorrência dos logos listados, "
            "mesmo que redimensionados, recoloridos, rotacionados "
            "ou distorcidos.\n"
            "- Identifique qualquer ocorrência dos textos listados, "
            "independente de fonte ou tamanho.\n"
            "- Para cada detecção, forneça bounding box como "
            "percentual das dimensões da imagem.\n\n"
            "Retorne um JSON com a seguinte estrutura:\n"
            "{\n"
            '  "detections": [\n'
            "    {\n"
            '      "match_type": "logo" | "text",\n'
            '      "confidence": <0-100>,\n'
            '      "bounding_box": {\n'
            '        "x_percent": <float>,\n'
            '        "y_percent": <float>,\n'
            '        "width_percent": <float>,\n'
            '        "height_percent": <float>\n'
            "      },\n"
            '      "description": "<descrição do que foi encontrado '
            'e contexto>"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            'Se nenhuma marca for detectada, retorne: '
            '{"detections": []}\n'
        )
        return prompt

    def _parse_detection_response(
        self,
        response: dict[str, Any],
        target_url: str,
        screenshot_ref_id: str,
    ) -> list[DetectionResult]:
        """Converte resposta JSON do Bedrock em DetectionResult objects.

        Parseia cada item da lista 'detections' da resposta,
        validando campos obrigatórios e convertendo para dataclass.

        Args:
            response: Dicionário JSON da resposta do Bedrock.
            target_url: URL do site-alvo associado.
            screenshot_ref_id: ID de referência do screenshot.

        Returns:
            Lista de DetectionResult (sem filtro de confidence).
        """
        detections: list[DetectionResult] = []

        raw_detections = response.get("detections", [])

        if not isinstance(raw_detections, list):
            logger.warning(
                "Resposta do Bedrock não contém lista de detecções "
                "válida para %s",
                target_url,
            )
            return detections

        now = datetime.now(timezone.utc)

        for idx, item in enumerate(raw_detections):
            try:
                detection = self._parse_single_detection(
                    item=item,
                    target_url=target_url,
                    screenshot_ref_id=screenshot_ref_id,
                    detected_at=now,
                )
                detections.append(detection)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Detecção #%d inválida na resposta para %s: %s",
                    idx,
                    target_url,
                    exc,
                )
                continue

        return detections

    def _parse_single_detection(
        self,
        item: dict[str, Any],
        target_url: str,
        screenshot_ref_id: str,
        detected_at: datetime,
    ) -> DetectionResult:
        """Parseia um item individual de detecção.

        Args:
            item: Dicionário com dados de uma detecção.
            target_url: URL do site-alvo.
            screenshot_ref_id: Referência do screenshot.
            detected_at: Timestamp da detecção.

        Returns:
            DetectionResult validado.

        Raises:
            KeyError: Se campo obrigatório estiver ausente.
            ValueError: Se valor for inválido.
            TypeError: Se tipo de dado for incorreto.
        """
        match_type = item["match_type"]
        if match_type not in ("logo", "text"):
            raise ValueError(
                f"match_type inválido: {match_type}"
            )

        confidence = int(item["confidence"])
        if not (0 <= confidence <= 100):
            raise ValueError(
                f"confidence fora do intervalo 0-100: {confidence}"
            )

        bbox_data = item["bounding_box"]
        bounding_box = BoundingBox(
            x_percent=float(bbox_data["x_percent"]),
            y_percent=float(bbox_data["y_percent"]),
            width_percent=float(bbox_data["width_percent"]),
            height_percent=float(bbox_data["height_percent"]),
        )

        description = str(item.get("description", ""))

        return DetectionResult(
            target_url=target_url,
            match_type=match_type,
            confidence=confidence,
            bounding_box=bounding_box,
            description=description,
            detected_at=detected_at,
            screenshot_ref_id=screenshot_ref_id,
        )
