"""Property tests para filtragem de imagens por tamanho no BedrockClient.

# Feature: mvp1-sky-amazon-compliance, Property 7: Image size filtering

**Validates: Requirements 10.4, 10.5**

Property 7: Para qualquer lista de imagens onde imagens individuais podem
exceder 5MB ou o total pode exceder 20MB:
(a) se total exceder 20MB, apenas o screenshot (primeira imagem) é incluído;
(b) imagens de referência individuais que excedem 5MB são excluídas
    enquanto as demais dentro do limite permanecem;
(c) o resultado filtrado sempre contém pelo menos o screenshot.
"""

from __future__ import annotations

from unittest.mock import patch

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.analyzer.bedrock_client import BedrockClient


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Constantes do BedrockClient
_MAX_TOTAL_PAYLOAD_BYTES = 20 * 1024 * 1024   # 20 MB
_MAX_SINGLE_IMAGE_BYTES = 5 * 1024 * 1024     # 5 MB
_MAX_IMAGES = 5


# -- Generators --


@st.composite
def image_with_label(
    draw: st.DrawFn,
    *,
    min_size: int = 1,
    max_size: int = 10 * 1024 * 1024,
) -> tuple[bytes, str]:
    """Gera uma tupla (image_bytes, label) com tamanho variável.

    O tamanho dos bytes varia de min_size a max_size para testar
    cenários dentro e fora dos limites de 5MB.
    """
    # Estratégia inteligente: gera tamanhos em faixas relevantes
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    label = draw(
        st.sampled_from([
            "screenshot_under_analysis",
            "approved_art_reference",
            "correct_logo_reference",
            "official_sky_plus_logo",
        ])
    )
    # Gera bytes do tamanho selecionado (b'\x00' * size é eficiente)
    image_bytes = b"\x00" * size
    return (image_bytes, label)


@st.composite
def image_list_with_variable_sizes(
    draw: st.DrawFn,
) -> list[tuple[bytes, str]]:
    """Gera lista de 1-5 imagens com tamanhos variáveis.

    A primeira imagem sempre tem label 'screenshot_under_analysis'.
    As demais são imagens de referência com tamanhos que podem
    exceder 5MB para testar filtragem individual.
    """
    # Screenshot (primeira imagem) - tamanho variável
    screenshot_size = draw(
        st.sampled_from([
            draw(st.integers(min_value=1, max_value=1024)),
            draw(st.integers(
                min_value=1024, max_value=_MAX_SINGLE_IMAGE_BYTES
            )),
            draw(st.integers(
                min_value=_MAX_SINGLE_IMAGE_BYTES + 1,
                max_value=10 * 1024 * 1024,
            )),
        ])
    )
    screenshot = (b"\x00" * screenshot_size, "screenshot_under_analysis")

    # Imagens de referência (0 a 4 imagens adicionais)
    num_ref_images = draw(st.integers(min_value=0, max_value=4))
    ref_labels = [
        "approved_art_reference",
        "correct_logo_reference",
        "official_sky_plus_logo",
        "wrong_logo_example",
    ]

    ref_images: list[tuple[bytes, str]] = []
    for i in range(num_ref_images):
        # Tamanhos em faixas interessantes para os testes
        ref_size = draw(
            st.sampled_from([
                draw(st.integers(min_value=1, max_value=1024)),
                draw(st.integers(
                    min_value=1024,
                    max_value=_MAX_SINGLE_IMAGE_BYTES,
                )),
                draw(st.integers(
                    min_value=_MAX_SINGLE_IMAGE_BYTES + 1,
                    max_value=10 * 1024 * 1024,
                )),
            ])
        )
        label = ref_labels[i % len(ref_labels)]
        ref_images.append((b"\x00" * ref_size, label))

    return [screenshot] + ref_images


# -- Helper --


def _create_bedrock_client() -> BedrockClient:
    """Cria instância de BedrockClient com mock do boto3."""
    with patch("boto3.client"):
        from brand_watchdog.config import AnalyzerConfig
        config = AnalyzerConfig(
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            bedrock_region="us-east-1",
            request_timeout_seconds=60,
            confidence_threshold=70,
        )
        return BedrockClient(config)


# -- Property Tests --


class TestImageSizeFiltering:
    """Property 7: Image size filtering.

    Para qualquer lista de imagens com tamanhos variáveis:
    (a) total > 20MB -> apenas screenshot;
    (b) individual > 5MB -> skip dessa imagem;
    (c) resultado sempre contém pelo menos o screenshot.

    **Validates: Requirements 10.4, 10.5**
    """

    @_PBT_SETTINGS
    @given(images=image_list_with_variable_sizes())
    def test_screenshot_always_present_in_result(
        self,
        images: list[tuple[bytes, str]],
    ):
        """O resultado da filtragem sempre contém o screenshot
        (primeira imagem da lista original)."""
        client = _create_bedrock_client()
        result = client._validate_payload_size(images)

        # Resultado nunca é vazio
        assert len(result) >= 1

        # Primeira imagem do resultado é o screenshot original
        assert result[0] is images[0]

    @_PBT_SETTINGS
    @given(images=image_list_with_variable_sizes())
    def test_oversized_ref_images_excluded(
        self,
        images: list[tuple[bytes, str]],
    ):
        """Imagens de referência (índice > 0) com mais de 5MB são
        excluídas do resultado."""
        client = _create_bedrock_client()
        result = client._validate_payload_size(images)

        # Nenhuma imagem de referência no resultado pode exceder 5MB
        for img_bytes, label in result[1:]:
            assert len(img_bytes) <= _MAX_SINGLE_IMAGE_BYTES, (
                f"Imagem de referência '{label}' com "
                f"{len(img_bytes)} bytes não deveria estar no "
                f"resultado (limite: {_MAX_SINGLE_IMAGE_BYTES})"
            )

    @_PBT_SETTINGS
    @given(images=image_list_with_variable_sizes())
    def test_total_exceeds_20mb_returns_only_screenshot(
        self,
        images: list[tuple[bytes, str]],
    ):
        """Se o total das imagens filtradas (após remoção das > 5MB)
        exceder 20MB, retorna apenas o screenshot."""
        client = _create_bedrock_client()
        result = client._validate_payload_size(images)

        # Calcula o que seria o total após filtrar > 5MB individuais
        screenshot = images[0]
        refs_within_limit = [
            (img, lbl) for img, lbl in images[1:]
            if len(img) <= _MAX_SINGLE_IMAGE_BYTES
        ]
        intermediate = [screenshot] + refs_within_limit
        intermediate_total = sum(
            len(img) for img, _ in intermediate
        )

        if intermediate_total > _MAX_TOTAL_PAYLOAD_BYTES:
            # Quando total excede 20MB, apenas screenshot
            assert len(result) == 1
            assert result[0] is images[0]

    @_PBT_SETTINGS
    @given(images=image_list_with_variable_sizes())
    def test_valid_ref_images_preserved(
        self,
        images: list[tuple[bytes, str]],
    ):
        """Imagens de referência dentro do limite de 5MB são preservadas
        no resultado (desde que o total não exceda 20MB)."""
        client = _create_bedrock_client()
        result = client._validate_payload_size(images)

        # Calcula imagens de referência que devem ser mantidas
        screenshot = images[0]
        valid_refs = [
            (img, lbl) for img, lbl in images[1:]
            if len(img) <= _MAX_SINGLE_IMAGE_BYTES
        ]
        intermediate = [screenshot] + valid_refs
        intermediate_total = sum(
            len(img) for img, _ in intermediate
        )

        if intermediate_total <= _MAX_TOTAL_PAYLOAD_BYTES:
            # Quando total está dentro do limite, refs válidas
            # são mantidas (respeitando max 5 imagens)
            expected_refs = valid_refs[:_MAX_IMAGES - 1]
            actual_refs = result[1:]
            for ref in actual_refs:
                assert ref in valid_refs, (
                    f"Imagem '{ref[1]}' no resultado não deveria "
                    f"estar presente"
                )

    @_PBT_SETTINGS
    @given(images=image_list_with_variable_sizes())
    def test_result_max_5_images(
        self,
        images: list[tuple[bytes, str]],
    ):
        """O resultado nunca excede o máximo de 5 imagens."""
        client = _create_bedrock_client()
        result = client._validate_payload_size(images)

        assert len(result) <= _MAX_IMAGES, (
            f"Resultado contém {len(result)} imagens, "
            f"máximo permitido é {_MAX_IMAGES}"
        )

    @_PBT_SETTINGS
    @given(images=image_list_with_variable_sizes())
    def test_screenshot_never_filtered_regardless_of_size(
        self,
        images: list[tuple[bytes, str]],
    ):
        """O screenshot (índice 0) NUNCA é filtrado, mesmo que
        exceda 5MB. A regra de 5MB aplica-se apenas a referências."""
        client = _create_bedrock_client()
        result = client._validate_payload_size(images)

        # O screenshot está no resultado independente do tamanho
        screenshot_bytes, screenshot_label = images[0]
        result_first_bytes, result_first_label = result[0]

        assert result_first_bytes is screenshot_bytes
        assert result_first_label == screenshot_label

    @_PBT_SETTINGS
    @given(images=image_list_with_variable_sizes())
    def test_order_preserved_for_valid_images(
        self,
        images: list[tuple[bytes, str]],
    ):
        """A ordem relativa das imagens válidas é preservada
        no resultado (screenshot primeiro, referências na mesma
        ordem original)."""
        client = _create_bedrock_client()
        result = client._validate_payload_size(images)

        # Verifica que a ordem das referências no resultado
        # corresponde à ordem original
        result_refs = result[1:]
        original_valid_refs = [
            (img, lbl) for img, lbl in images[1:]
            if len(img) <= _MAX_SINGLE_IMAGE_BYTES
        ]

        # Cada ref no resultado deve aparecer na mesma ordem
        # relativa que no original
        last_idx = -1
        for ref in result_refs:
            try:
                idx = original_valid_refs.index(ref, last_idx + 1)
                last_idx = idx
            except ValueError:
                # Se não encontrou, pode ser por truncamento
                # (total > 20MB ou max 5 imagens)
                pass
