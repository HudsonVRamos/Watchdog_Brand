"""Property tests para resiliência do PromptBuilder a imagens ausentes.

# Feature: mvp1-sky-amazon-compliance, Property 8: Prompt builder resilience to missing reference images

**Validates: Requirements 6.7, 6.8**

Property 8: Para qualquer subconjunto de imagens de referência ausentes
(de 0 a todas as 3), o CompliancePromptBuilder deve produzir um PromptPayload
válido contendo: screenshot como primeira imagem com label
"screenshot_under_analysis", apenas imagens de referência disponíveis,
total de imagens == 1 (screenshot) + count(imagens disponíveis), e texto
completo com todas as 5 seções de regras.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.analyzer.compliance_prompt_builder import (
    CompliancePromptBuilder,
    PromptPayload,
)


_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Nomes dos arquivos de referência do builder
_ALL_REFERENCE_IMAGES = list(CompliancePromptBuilder.REFERENCE_IMAGES.keys())

# As 5 seções de regras que devem sempre estar presentes no prompt
_RULE_SECTIONS = [
    "FACILITATOR_ROLE",
    "LOGO_APPLICATION",
    "CONTENT_SEPARATION",
    "NAMING_PRICING",
    "KV_INTEGRITY",
]


# -- Generator --


@st.composite
def reference_image_availability(
    draw: st.DrawFn,
) -> frozenset[str]:
    """Gera um subconjunto de imagens de referência que estarão 'presentes'.

    Retorna um frozenset com 0 a 3 nomes de arquivos de referência que
    devem existir no filesystem para o teste. As demais serão ausentes.
    """
    available = draw(
        st.frozensets(
            st.sampled_from(_ALL_REFERENCE_IMAGES),
            min_size=0,
            max_size=len(_ALL_REFERENCE_IMAGES),
        )
    )
    return available


# -- Helper --


def _setup_test_directory(
    base_dir: Path,
    available_images: frozenset[str],
) -> tuple[Path, Path]:
    """Cria estrutura de diretório temporário com imagens disponíveis.

    Retorna (screenshot_path, rules_base_path).
    """
    # Criar screenshot válido (sempre presente)
    screenshot_path = base_dir / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake_screenshot_data")

    # Criar diretório de imagens de referência
    images_dir = base_dir / "watchdog_rules" / "SKY_Amazon_Imagens"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Criar apenas imagens marcadas como disponíveis
    for filename in available_images:
        image_path = images_dir / filename
        image_path.write_bytes(
            b"\x89PNG\r\n\x1a\n" + f"fake_{filename}_data".encode()
        )

    return screenshot_path, base_dir


# -- Property Test --


class TestPromptBuilderResilienceToMissingImages:
    """Property 8: Prompt builder resilience to missing reference images.

    Para qualquer subconjunto de imagens de referência disponíveis (0 a 3),
    o PromptPayload produzido deve conter:
    - Screenshot como primeira imagem com label "screenshot_under_analysis"
    - Apenas imagens de referência que existem no filesystem
    - Total de imagens == 1 + count(imagens disponíveis)
    - Texto completo com todas as 5 seções de regras

    **Validates: Requirements 6.7, 6.8**
    """

    @_PBT_SETTINGS
    @given(available_images=reference_image_availability())
    def test_payload_always_valid_regardless_of_available_images(
        self,
        available_images: frozenset[str],
    ):
        """Para qualquer combinação de imagens presentes/ausentes, o
        PromptPayload resultante deve ser válido."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            screenshot_path, rules_base = _setup_test_directory(
                tmp_path, available_images
            )

            builder = CompliancePromptBuilder(rules_base_path=rules_base)
            payload = builder.build_prompt(screenshot_path)

            # Verificar que payload é instância de PromptPayload
            assert isinstance(payload, PromptPayload)

            # Verificar que imagens não está vazia (sempre tem screenshot)
            assert len(payload.images) >= 1

            # Verificar screenshot é a primeira imagem
            first_image_bytes, first_label = payload.images[0]
            assert first_label == "screenshot_under_analysis"
            assert len(first_image_bytes) > 0

            # Verificar contagem total de imagens
            expected_count = 1 + len(available_images)
            assert len(payload.images) == expected_count, (
                f"Esperado {expected_count} imagens "
                f"(1 screenshot + {len(available_images)} referências), "
                f"obteve {len(payload.images)}"
            )

            # Verificar que prompt_text não está vazio
            assert payload.prompt_text
            assert len(payload.prompt_text) > 0

    @_PBT_SETTINGS
    @given(available_images=reference_image_availability())
    def test_only_available_reference_images_included(
        self,
        available_images: frozenset[str],
    ):
        """Apenas imagens de referência que existem são incluídas no payload."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            screenshot_path, rules_base = _setup_test_directory(
                tmp_path, available_images
            )

            builder = CompliancePromptBuilder(rules_base_path=rules_base)
            payload = builder.build_prompt(screenshot_path)

            # Labels das imagens de referência no payload (excluindo screenshot)
            ref_labels_in_payload = [
                label for _, label in payload.images[1:]
            ]

            # Labels esperados baseados nas imagens disponíveis
            expected_labels = [
                CompliancePromptBuilder.REFERENCE_IMAGES[filename]
                for filename in _ALL_REFERENCE_IMAGES
                if filename in available_images
            ]

            assert set(ref_labels_in_payload) == set(expected_labels), (
                f"Labels no payload: {ref_labels_in_payload}, "
                f"esperados: {expected_labels}"
            )

    @_PBT_SETTINGS
    @given(available_images=reference_image_availability())
    def test_rules_text_always_contains_all_five_sections(
        self,
        available_images: frozenset[str],
    ):
        """O texto de regras deve conter todas as 5 seções de compliance
        independente de quantas imagens estão disponíveis."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            screenshot_path, rules_base = _setup_test_directory(
                tmp_path, available_images
            )

            builder = CompliancePromptBuilder(rules_base_path=rules_base)
            payload = builder.build_prompt(screenshot_path)

            # Verificar que cada seção de regras está presente no texto
            for section_name in _RULE_SECTIONS:
                assert section_name in payload.prompt_text, (
                    f"Seção '{section_name}' ausente no prompt_text. "
                    f"Imagens disponíveis: {available_images}"
                )

    @_PBT_SETTINGS
    @given(available_images=reference_image_availability())
    def test_screenshot_bytes_are_non_empty(
        self,
        available_images: frozenset[str],
    ):
        """O screenshot no payload deve sempre conter bytes não-vazios."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            screenshot_path, rules_base = _setup_test_directory(
                tmp_path, available_images
            )

            builder = CompliancePromptBuilder(rules_base_path=rules_base)
            payload = builder.build_prompt(screenshot_path)

            screenshot_bytes, screenshot_label = payload.images[0]
            assert screenshot_label == "screenshot_under_analysis"
            assert len(screenshot_bytes) > 0

            # Cada imagem de referência presente também deve ter bytes
            for img_bytes, img_label in payload.images[1:]:
                assert len(img_bytes) > 0, (
                    f"Imagem com label '{img_label}' tem bytes vazios"
                )
