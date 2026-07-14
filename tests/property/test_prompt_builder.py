"""Property tests para ordenação do payload do CompliancePromptBuilder.

# Feature: architecture-evolution, Property 14: Ordenação do Payload para Prompt Caching

**Validates: Requirements 8.3**

Property 14: Para qualquer payload construído pelo CompliancePromptBuilder,
os content blocks de conteúdo estático (regras textuais + imagens de referência)
SHALL preceder todos os content blocks de conteúdo variável (screenshot do site)
na lista de content blocks.
"""

from __future__ import annotations

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from brand_watchdog.analyzer.compliance_prompt_builder import (
    CompliancePromptBuilder,
    PromptPayload,
)


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Labels que representam conteúdo variável (screenshot)
_VARIABLE_LABELS = {"screenshot_under_analysis"}

# Brands suportados pelo builder
_BRANDS = ["sky_plus", "dgo"]


# -- Strategies --


@st.composite
def reference_images_strategy(draw: st.DrawFn) -> list[tuple[bytes, str]]:
    """Gera uma lista de imagens de referência simuladas (1 a 5 imagens).

    Cada imagem é uma tupla (bytes_jpeg, label) representando imagens
    já processadas pelo ReferenceImageCache (formato JPEG).
    """
    num_images = draw(st.integers(min_value=1, max_value=5))
    images: list[tuple[bytes, str]] = []
    for i in range(num_images):
        # Gerar bytes não-vazios simulando conteúdo JPEG
        img_bytes = draw(
            st.binary(min_size=10, max_size=200)
        )
        label = draw(
            st.sampled_from([
                "approved_art_reference",
                "correct_logo_reference",
                "official_sky_plus_logo",
                "official_brand_logo",
                f"reference_image_{i}",
            ])
        )
        images.append((img_bytes, label))
    return images


@st.composite
def screenshot_bytes_strategy(draw: st.DrawFn) -> bytes:
    """Gera bytes não-vazios simulando um screenshot PNG."""
    return draw(st.binary(min_size=10, max_size=500))


@st.composite
def prompt_cached_inputs(
    draw: st.DrawFn,
) -> tuple[bytes, list[tuple[bytes, str]], str]:
    """Gera inputs válidos para build_prompt_cached.

    Retorna: (screenshot_bytes, reference_images, brand)
    """
    screenshot = draw(screenshot_bytes_strategy())
    references = draw(reference_images_strategy())
    brand = draw(st.sampled_from(_BRANDS))
    return screenshot, references, brand


# -- Property Tests --


class TestPayloadOrderingForPromptCaching:
    """Property 14: Ordenação do Payload para Prompt Caching.

    Para qualquer payload construído pelo CompliancePromptBuilder
    (método build_prompt_cached), os content blocks de conteúdo
    estático (imagens de referência) SHALL preceder todos os content
    blocks de conteúdo variável (screenshot do site) na lista de
    content blocks.

    **Validates: Requirements 8.3**
    """

    @_PBT_SETTINGS
    @given(data=prompt_cached_inputs())
    def test_static_content_precedes_variable_content(
        self,
        data: tuple[bytes, list[tuple[bytes, str]], str],
    ):
        """Conteúdo estático (referências) precede conteúdo variável
        (screenshot) na lista de content blocks."""
        screenshot_bytes, reference_images, brand = data

        builder = CompliancePromptBuilder(brand=brand)
        payload = builder.build_prompt_cached(
            screenshot_bytes=screenshot_bytes,
            reference_images=reference_images,
        )

        assert isinstance(payload, PromptPayload)
        assert len(payload.images) > 0

        # Encontrar o índice do screenshot (conteúdo variável)
        screenshot_indices = [
            i
            for i, (_, label) in enumerate(payload.images)
            if label in _VARIABLE_LABELS
        ]
        assert len(screenshot_indices) == 1, (
            f"Esperado exatamente 1 screenshot no payload, "
            f"encontrado {len(screenshot_indices)}"
        )
        screenshot_idx = screenshot_indices[0]

        # Encontrar índices de conteúdo estático (referências)
        static_indices = [
            i
            for i, (_, label) in enumerate(payload.images)
            if label not in _VARIABLE_LABELS
        ]

        # PROPRIEDADE: Todos os blocos estáticos devem ter índice
        # MENOR que o bloco variável (screenshot)
        for static_idx in static_indices:
            assert static_idx < screenshot_idx, (
                f"Bloco estático no índice {static_idx} (label="
                f"'{payload.images[static_idx][1]}') NÃO precede "
                f"o screenshot no índice {screenshot_idx}. "
                f"Ordem dos labels: "
                f"{[label for _, label in payload.images]}"
            )

    @_PBT_SETTINGS
    @given(data=prompt_cached_inputs())
    def test_screenshot_is_last_content_block(
        self,
        data: tuple[bytes, list[tuple[bytes, str]], str],
    ):
        """O screenshot (conteúdo variável) deve ser o último content
        block na lista de imagens."""
        screenshot_bytes, reference_images, brand = data

        builder = CompliancePromptBuilder(brand=brand)
        payload = builder.build_prompt_cached(
            screenshot_bytes=screenshot_bytes,
            reference_images=reference_images,
        )

        # O último bloco deve ser o screenshot
        last_bytes, last_label = payload.images[-1]
        assert last_label == "screenshot_under_analysis", (
            f"Último bloco deveria ser 'screenshot_under_analysis', "
            f"mas é '{last_label}'. "
            f"Ordem: {[label for _, label in payload.images]}"
        )

    @_PBT_SETTINGS
    @given(data=prompt_cached_inputs())
    def test_cache_control_index_points_to_last_static_block(
        self,
        data: tuple[bytes, list[tuple[bytes, str]], str],
    ):
        """O cache_control_index deve apontar para o último bloco
        estático (última referência antes do screenshot), habilitando
        Prompt Caching no Bedrock."""
        screenshot_bytes, reference_images, brand = data

        builder = CompliancePromptBuilder(brand=brand)
        payload = builder.build_prompt_cached(
            screenshot_bytes=screenshot_bytes,
            reference_images=reference_images,
        )

        # cache_control_index deve existir quando há referências
        assert payload.cache_control_index is not None, (
            "cache_control_index deveria ser definido quando há "
            "imagens de referência"
        )

        # O índice deve apontar para a última imagem de referência
        # (que é o bloco imediatamente antes do screenshot)
        expected_index = len(reference_images) - 1
        assert payload.cache_control_index == expected_index, (
            f"cache_control_index={payload.cache_control_index}, "
            f"esperado={expected_index} "
            f"(última referência antes do screenshot)"
        )

        # O bloco no cache_control_index NÃO deve ser o screenshot
        _, label_at_index = payload.images[payload.cache_control_index]
        assert label_at_index not in _VARIABLE_LABELS, (
            f"cache_control_index aponta para bloco variável "
            f"'{label_at_index}', deveria apontar para bloco estático"
        )

    @_PBT_SETTINGS
    @given(data=prompt_cached_inputs())
    def test_static_blocks_use_jpeg_media_type(
        self,
        data: tuple[bytes, list[tuple[bytes, str]], str],
    ):
        """Blocos estáticos (referências) devem usar media_type
        'image/jpeg' e o screenshot deve usar 'image/png'."""
        screenshot_bytes, reference_images, brand = data

        builder = CompliancePromptBuilder(brand=brand)
        payload = builder.build_prompt_cached(
            screenshot_bytes=screenshot_bytes,
            reference_images=reference_images,
        )

        assert payload.media_types is not None, (
            "media_types deveria ser definido no modo cached"
        )
        assert len(payload.media_types) == len(payload.images), (
            f"media_types ({len(payload.media_types)}) deve ter "
            f"mesmo tamanho que images ({len(payload.images)})"
        )

        # Referências (estáticas) usam JPEG
        for i in range(len(reference_images)):
            assert payload.media_types[i] == "image/jpeg", (
                f"media_type[{i}] deveria ser 'image/jpeg', "
                f"mas é '{payload.media_types[i]}'"
            )

        # Screenshot (variável) usa PNG
        assert payload.media_types[-1] == "image/png", (
            f"media_type do screenshot deveria ser 'image/png', "
            f"mas é '{payload.media_types[-1]}'"
        )
