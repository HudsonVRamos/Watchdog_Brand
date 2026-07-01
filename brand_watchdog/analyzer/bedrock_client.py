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
