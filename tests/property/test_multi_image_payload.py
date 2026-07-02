"""Property tests para construção de payload multi-imagem com labels.

# Feature: mvp1-sky-amazon-compliance, Property 6: Multi-image payload construction with labels

**Validates: Requirements 10.1, 10.2**

Property 6: Para qualquer lista de 1-5 tuples (image_bytes, label) onde cada
imagem tem ≤ 5MB e o total ≤ 20MB, o payload construído pelo BedrockClient
deve conter cada imagem como um bloco de imagem precedido por um bloco de texto
com seu label, na mesma ordem da lista de entrada, com o prompt no final.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.analyzer.bedrock_client import BedrockClient
from brand_watchdog.config import AnalyzerConfig


_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Limites de tamanho conforme design
_MAX_SINGLE_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
_MAX_TOTAL_PAYLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
_MAX_IMAGES = 5


# -- Generator --


@st.composite
def image_with_label(draw: st.DrawFn) -> tuple[bytes, str]:
    """Gera uma tupla (image_bytes, label) dentro dos limites.

    - image_bytes: entre 1 byte e 100KB (viável para testes rápidos,
      mas dentro do limite de 5MB)
    - label: string não-vazia com 1-50 caracteres
    """
    image_bytes = draw(
        st.binary(min_size=1, max_size=100 * 1024)
    )
    label = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P"),
            ),
            min_size=1,
            max_size=50,
        )
    )
    return (image_bytes, label)


@st.composite
def image_list_with_labels(
    draw: st.DrawFn,
) -> list[tuple[bytes, str]]:
    """Gera lista de 1-5 tuples (image_bytes, label) dentro dos limites.

    Garante que:
    - Cada imagem tem ≤ 5MB
    - Total de todas as imagens ≤ 20MB
    - Entre 1 e 5 imagens
    """
    images = draw(
        st.lists(
            image_with_label(),
            min_size=1,
            max_size=_MAX_IMAGES,
        )
    )
    # Filtrar para garantir conformidade com limites
    # (os dados gerados já são pequenos, mas validamos para robustez)
    filtered = []
    total_size = 0
    for img_bytes, label in images:
        if len(img_bytes) > _MAX_SINGLE_IMAGE_BYTES:
            continue
        if total_size + len(img_bytes) > _MAX_TOTAL_PAYLOAD_BYTES:
            break
        filtered.append((img_bytes, label))
        total_size += len(img_bytes)

    # Garantir pelo menos 1 imagem
    if not filtered:
        filtered = [images[0]]

    return filtered


@st.composite
def prompt_text(draw: st.DrawFn) -> str:
    """Gera um prompt textual não-vazio."""
    return draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "Z"),
            ),
            min_size=1,
            max_size=200,
        )
    )


# -- Helper --


def _create_bedrock_client() -> BedrockClient:
    """Cria um BedrockClient com configuração dummy, mockando boto3."""
    config = AnalyzerConfig(
        bedrock_model_id="anthropic.claude-sonnet-4-6",
        bedrock_region="us-east-1",
        confidence_threshold=70,
        request_timeout_seconds=60,
        max_retries=3,
        retry_base_delay_seconds=2.0,
    )
    with patch("brand_watchdog.analyzer.bedrock_client.boto3.client"):
        client = BedrockClient(config)
    return client


# -- Property Tests --


class TestMultiImagePayloadConstruction:
    """Property 6: Multi-image payload construction with labels.

    Para qualquer lista de 1-5 tuples (image_bytes, label) dentro dos
    limites de tamanho, o payload construído deve:
    - Conter cada imagem com seu label na ordem correta
    - Ter blocos de texto (label) nas posições pares: i*2
    - Ter blocos de imagem nas posições ímpares: i*2+1
    - Ter o prompt como último elemento do content array
    - Cada imagem deve estar codificada em base64

    **Validates: Requirements 10.1, 10.2**
    """

    @_PBT_SETTINGS
    @given(
        images=image_list_with_labels(),
        prompt=prompt_text(),
    )
    def test_labels_precede_images_in_correct_order(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
    ):
        """Para cada imagem no índice i, o content array tem um bloco de
        texto com o label na posição i*2 seguido do bloco de imagem em
        i*2+1."""
        client = _create_bedrock_client()
        payload = client._build_multi_image_payload(images, prompt)

        content = payload["messages"][0]["content"]

        for i, (img_bytes, label) in enumerate(images):
            text_pos = i * 2
            image_pos = i * 2 + 1

            # Bloco de texto com label na posição correta
            assert content[text_pos]["type"] == "text", (
                f"Posição {text_pos} deveria ser 'text', "
                f"obteve '{content[text_pos]['type']}'"
            )
            assert content[text_pos]["text"] == label, (
                f"Label na posição {text_pos} deveria ser '{label}', "
                f"obteve '{content[text_pos]['text']}'"
            )

            # Bloco de imagem na posição seguinte
            assert content[image_pos]["type"] == "image", (
                f"Posição {image_pos} deveria ser 'image', "
                f"obteve '{content[image_pos]['type']}'"
            )

    @_PBT_SETTINGS
    @given(
        images=image_list_with_labels(),
        prompt=prompt_text(),
    )
    def test_prompt_is_last_element_in_content(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
    ):
        """O prompt deve ser o último elemento do content array."""
        client = _create_bedrock_client()
        payload = client._build_multi_image_payload(images, prompt)

        content = payload["messages"][0]["content"]
        last_element = content[-1]

        assert last_element["type"] == "text", (
            f"Último elemento deveria ser 'text', "
            f"obteve '{last_element['type']}'"
        )
        assert last_element["text"] == prompt, (
            f"Texto do último elemento deveria ser o prompt"
        )

    @_PBT_SETTINGS
    @given(
        images=image_list_with_labels(),
        prompt=prompt_text(),
    )
    def test_images_are_base64_encoded(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
    ):
        """Cada imagem no payload deve estar codificada em base64 válido,
        e ao decodificar deve ser igual aos bytes originais."""
        client = _create_bedrock_client()
        payload = client._build_multi_image_payload(images, prompt)

        content = payload["messages"][0]["content"]

        for i, (img_bytes, _label) in enumerate(images):
            image_pos = i * 2 + 1
            image_block = content[image_pos]

            # Verificar estrutura do bloco de imagem
            assert image_block["type"] == "image"
            source = image_block["source"]
            assert source["type"] == "base64"
            assert source["media_type"] == "image/png"

            # Verificar que base64 decodifica para os bytes originais
            decoded = base64.b64decode(source["data"])
            assert decoded == img_bytes, (
                f"Imagem no índice {i} não decodifica para os bytes "
                f"originais. Tamanho original: {len(img_bytes)}, "
                f"decodificado: {len(decoded)}"
            )

    @_PBT_SETTINGS
    @given(
        images=image_list_with_labels(),
        prompt=prompt_text(),
    )
    def test_content_array_has_correct_length(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
    ):
        """O content array deve ter exatamente (n_images * 2) + 1 elementos:
        para cada imagem um text + image block, mais o prompt final."""
        client = _create_bedrock_client()
        payload = client._build_multi_image_payload(images, prompt)

        content = payload["messages"][0]["content"]
        expected_length = len(images) * 2 + 1

        assert len(content) == expected_length, (
            f"Content array deveria ter {expected_length} elementos "
            f"({len(images)} imagens * 2 + 1 prompt), "
            f"obteve {len(content)}"
        )

    @_PBT_SETTINGS
    @given(
        images=image_list_with_labels(),
        prompt=prompt_text(),
    )
    def test_payload_has_correct_structure(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
    ):
        """O payload deve ter a estrutura correta para Anthropic Messages API:
        anthropic_version, max_tokens, messages com role 'user'."""
        client = _create_bedrock_client()
        payload = client._build_multi_image_payload(images, prompt)

        assert payload["anthropic_version"] == "bedrock-2023-05-31"
        assert payload["max_tokens"] == 4096
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"
        assert "content" in payload["messages"][0]
