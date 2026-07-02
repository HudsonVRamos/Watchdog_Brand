"""Cliente para AWS Bedrock (Claude) para análise multimodal.

Encapsula a invocação do modelo Claude via Bedrock Runtime,
com retry automático (exponential backoff) e timeout configurável.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from brand_watchdog.config import AnalyzerConfig

logger = logging.getLogger(__name__)


class BedrockClient:
    """Cliente para invocação do modelo Claude via AWS Bedrock.

    Attributes:
        _config: Configuração do analisador com model_id, região e timeouts.
        _client: Cliente boto3 bedrock-runtime configurado.
    """

    def __init__(self, config: AnalyzerConfig) -> None:
        self._config = config
        boto_config = BotoConfig(
            read_timeout=config.request_timeout_seconds,
            connect_timeout=10,
            retries={"max_attempts": 0},  # Retry gerenciado pelo Tenacity
        )
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=config.bedrock_region,
            config=boto_config,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        retry=retry_if_exception_type(
            (BotoCoreError, ClientError, TimeoutError)
        ),
        reraise=True,
    )
    async def invoke_model(
        self, image_bytes: bytes, prompt: str
    ) -> dict[str, Any]:
        """Invoca Claude via Bedrock com imagem e prompt.

        Constrói o payload no formato Anthropic Messages API,
        codifica a imagem em base64, e envia para o modelo.
        Retry automático com backoff exponencial: 2s, 4s, 8s.

        Args:
            image_bytes: Bytes da imagem PNG para análise.
            prompt: Prompt textual com instruções de análise.

        Returns:
            Dicionário JSON parseado da resposta do modelo.

        Raises:
            BotoCoreError: Erro de infraestrutura AWS.
            ClientError: Erro do serviço Bedrock.
            TimeoutError: Timeout de 60 segundos excedido.
            ValueError: Resposta do modelo não contém JSON válido.
        """
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_base64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

        logger.debug(
            "Invocando Bedrock modelo=%s, imagem=%d bytes",
            self._config.bedrock_model_id,
            len(image_bytes),
        )

        # boto3 é síncrono, usamos asyncio.to_thread para não bloquear
        response = await asyncio.to_thread(
            self._client.invoke_model,
            modelId=self._config.bedrock_model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )

        response_body = json.loads(response["body"].read())
        return self._extract_json_from_response(response_body)

    def _extract_json_from_response(
        self, response_body: dict[str, Any]
    ) -> dict[str, Any]:
        """Extrai JSON estruturado da resposta do Claude.

        O Claude retorna o texto dentro de content[0].text.
        O texto pode estar envolto em blocos markdown ```json ... ```.

        Args:
            response_body: Corpo da resposta do Bedrock (já parseado).

        Returns:
            Dicionário com o JSON extraído da resposta.

        Raises:
            ValueError: Se não for possível extrair JSON válido.
        """
        try:
            content = response_body["content"]
            text = content[0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                "Resposta do Bedrock não contém content[0].text"
            ) from exc

        # Remove blocos markdown ```json ... ``` se presentes
        cleaned = self._strip_markdown_code_block(text)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Resposta do modelo não é JSON válido: {cleaned[:200]}"
            ) from exc

    # Constantes para validação de payload multi-imagem
    MAX_TOTAL_PAYLOAD_BYTES: int = 20 * 1024 * 1024  # 20 MB
    MAX_SINGLE_IMAGE_BYTES: int = 5 * 1024 * 1024    # 5 MB
    MAX_IMAGES: int = 5

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        retry=retry_if_exception_type(
            (BotoCoreError, ClientError, TimeoutError)
        ),
        reraise=True,
    )
    async def invoke_model_multi(
        self, images: list[tuple[bytes, str]], prompt: str
    ) -> dict[str, Any]:
        """Invoca Claude via Bedrock com múltiplas imagens e prompt.

        Constrói o payload multimodal com labels de texto antes de cada
        imagem, seguidos do prompt no final. Suporta até 5 imagens.
        Retry automático com backoff exponencial: 2s, 4s, 8s.

        Args:
            images: Lista de tuplas (image_bytes, label) onde label identifica
                o papel da imagem. A primeira imagem é sempre o screenshot.
                Labels esperados: "screenshot_under_analysis",
                "approved_art_reference", "correct_logo_reference",
                "wrong_logo_example".
            prompt: Prompt textual com instruções de análise.

        Returns:
            Dicionário JSON parseado da resposta do modelo.

        Raises:
            BotoCoreError: Erro de infraestrutura AWS.
            ClientError: Erro do serviço Bedrock.
            TimeoutError: Timeout de 60 segundos excedido.
            ValueError: Resposta do modelo não contém JSON válido.
        """
        validated_images = self._validate_payload_size(images)
        request_body = self._build_multi_image_payload(validated_images, prompt)

        total_size = sum(len(img) for img, _ in validated_images)
        logger.debug(
            "Invocando Bedrock multi-image modelo=%s, imagens=%d, total=%d bytes",
            self._config.bedrock_model_id,
            len(validated_images),
            total_size,
        )

        response = await asyncio.to_thread(
            self._client.invoke_model,
            modelId=self._config.bedrock_model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )

        response_body = json.loads(response["body"].read())
        return self._extract_json_from_response(response_body)

    def _build_multi_image_payload(
        self, images: list[tuple[bytes, str]], prompt: str
    ) -> dict[str, Any]:
        """Constrói payload multimodal com labels de texto antes de cada imagem.

        Formato do content array:
        [
            {"type": "text", "text": label_1},
            {"type": "image", "source": {"type": "base64", ...}},
            {"type": "text", "text": label_2},
            {"type": "image", "source": {"type": "base64", ...}},
            ...
            {"type": "text", "text": prompt}
        ]

        Args:
            images: Lista de tuplas (image_bytes, label) já validadas.
            prompt: Prompt textual para análise.

        Returns:
            Dicionário com o payload completo para o Bedrock Messages API.
        """
        content: list[dict[str, Any]] = []

        for image_bytes, label in images:
            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            # Text block com o label antes da imagem
            content.append({"type": "text", "text": label})
            # Image block
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_base64,
                    },
                }
            )

        # Prompt no final do content array
        content.append({"type": "text", "text": prompt})

        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }

    def _validate_payload_size(
        self, images: list[tuple[bytes, str]]
    ) -> list[tuple[bytes, str]]:
        """Filtra imagens por tamanho, mantendo sempre o screenshot (índice 0).

        Regras:
        - A primeira imagem (screenshot) NUNCA é filtrada.
        - Imagens de referência (índice > 0) com mais de 5 MB são descartadas
          com log warning.
        - Se o total de todas as imagens (incluindo screenshot) exceder 20 MB,
          retorna apenas o screenshot com log error.

        Args:
            images: Lista de tuplas (image_bytes, label). Índice 0 é o
                screenshot.

        Returns:
            Lista filtrada de tuplas (image_bytes, label), sempre contendo
            pelo menos o screenshot (índice 0).
        """
        if not images:
            return images

        # Sempre incluir o screenshot (índice 0)
        screenshot = images[0]
        filtered: list[tuple[bytes, str]] = [screenshot]

        # Filtrar imagens de referência individuais > 5 MB
        for i, (img_bytes, label) in enumerate(images[1:], start=1):
            img_size = len(img_bytes)
            if img_size > self.MAX_SINGLE_IMAGE_BYTES:
                logger.warning(
                    "Imagem de referência '%s' (índice %d) excede 5 MB "
                    "(%d bytes). Descartando.",
                    label,
                    i,
                    img_size,
                )
            else:
                filtered.append((img_bytes, label))

        # Verificar total do payload
        total_size = sum(len(img) for img, _ in filtered)
        if total_size > self.MAX_TOTAL_PAYLOAD_BYTES:
            logger.error(
                "Payload total excede 20 MB (%d bytes). "
                "Enviando apenas o screenshot.",
                total_size,
            )
            return [screenshot]

        # Limitar ao máximo de 5 imagens
        if len(filtered) > self.MAX_IMAGES:
            logger.warning(
                "Número de imagens (%d) excede o máximo de %d. "
                "Truncando para %d imagens.",
                len(filtered),
                self.MAX_IMAGES,
                self.MAX_IMAGES,
            )
            filtered = filtered[: self.MAX_IMAGES]

        return filtered

    @staticmethod
    def _strip_markdown_code_block(text: str) -> str:
        """Remove wrapper de code block markdown do texto.

        Suporta formatos:
        - ```json\\n{...}\\n```
        - ```\\n{...}\\n```
        - Texto com prosa antes/depois do code block
        - Texto puro sem wrapper (JSON direto)

        Args:
            text: Texto potencialmente envolto em code block.

        Returns:
            Texto limpo sem delimitadores de code block.
        """
        stripped = text.strip()

        # Padrão 1: texto inteiro é um code block
        pattern_full = r"^```(?:json)?\s*\n?(.*?)\n?\s*```$"
        match = re.match(pattern_full, stripped, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Padrão 2: code block em algum lugar do texto
        # (Claude pode adicionar texto antes/depois)
        pattern_inner = r"```(?:json)?\s*\n(.*?)\n\s*```"
        match = re.search(pattern_inner, stripped, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Padrão 3: tenta encontrar JSON bruto ({...})
        pattern_json = r"\{[^{}]*\"detections\"[^{}]*\[.*?\]\s*\}"
        match = re.search(pattern_json, stripped, re.DOTALL)
        if match:
            return match.group(0).strip()

        return stripped
