"""Testes unitários para CompliancePromptBuilder.

Valida construção de prompt multimodal com regras de compliance,
carregamento de imagens de referência, e tratamento de cenários
com imagens presentes/ausentes.

Requisitos cobertos: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brand_watchdog.analyzer.compliance_exceptions import AnalysisIncompleteError
from brand_watchdog.analyzer.compliance_prompt_builder import (
    CompliancePromptBuilder,
    PromptPayload,
)


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    """Cria diretório base com estrutura de imagens de referência."""
    images_dir = tmp_path / "watchdog_rules" / "SKY_Amazon_Imagens"
    images_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def all_reference_images(base_path: Path) -> Path:
    """Cria todas as 3 imagens de referência no diretório."""
    images_dir = base_path / "watchdog_rules" / "SKY_Amazon_Imagens"

    # Criar arquivos PNG simulados com conteúdo não-vazio
    (images_dir / "Artes_aprovadas_referencia.PNG").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"artes_aprovadas_content"
    )
    (images_dir / "Logo_errado_logo_correto.PNG").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"logo_errado_correto_content"
    )
    (images_dir / "logo_sky_plus_amazon.PNG").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"sky_plus_amazon_content"
    )

    return base_path


@pytest.fixture
def screenshot_file(tmp_path: Path) -> Path:
    """Cria um arquivo de screenshot válido."""
    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"screenshot_data"
    )
    return screenshot


@pytest.fixture
def builder_all_images(all_reference_images: Path) -> CompliancePromptBuilder:
    """Builder com todas as imagens de referência disponíveis."""
    return CompliancePromptBuilder(rules_base_path=all_reference_images)


@pytest.fixture
def builder_no_images(base_path: Path) -> CompliancePromptBuilder:
    """Builder sem nenhuma imagem de referência (diretório vazio)."""
    return CompliancePromptBuilder(rules_base_path=base_path)


class TestRulesSections:
    """Testes para validar que o prompt contém exatamente 5 seções de regras."""

    def test_prompt_contains_exactly_5_rule_sections(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt text deve conter exatamente 5 seções numeradas de regras."""
        payload = builder_all_images.build_prompt(screenshot_file)

        # Verificar presença das 5 seções pelo header markdown
        assert "### 1. FACILITATOR_ROLE" in payload.prompt_text
        assert "### 2. LOGO_APPLICATION" in payload.prompt_text
        assert "### 3. CONTENT_SEPARATION" in payload.prompt_text
        assert "### 4. NAMING_PRICING" in payload.prompt_text
        assert "### 5. KV_INTEGRITY" in payload.prompt_text

    def test_prompt_does_not_contain_extra_sections(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt não deve conter seções numeradas além das 5 esperadas."""
        payload = builder_all_images.build_prompt(screenshot_file)

        # Não deve existir seção 6 ou superior de regras
        assert "### 6." not in payload.prompt_text
        assert "### 7." not in payload.prompt_text

    def test_rules_text_contains_all_rule_topics(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Texto de regras aborda todos os tópicos de compliance."""
        payload = builder_all_images.build_prompt(screenshot_file)

        # Tópicos obrigatórios no texto de regras
        assert "facilitator_role" in payload.prompt_text.lower() or \
            "FACILITATOR_ROLE" in payload.prompt_text
        assert "logo_application" in payload.prompt_text.lower() or \
            "LOGO_APPLICATION" in payload.prompt_text
        assert "logo_effects" in payload.prompt_text.lower() or \
            "LOGO_EFFECTS" in payload.prompt_text
        assert "content_separation" in payload.prompt_text.lower() or \
            "CONTENT_SEPARATION" in payload.prompt_text
        assert "naming_pricing" in payload.prompt_text.lower() or \
            "NAMING_PRICING" in payload.prompt_text
        assert "kv_integrity" in payload.prompt_text.lower() or \
            "KV_INTEGRITY" in payload.prompt_text


class TestScreenshotFirstImage:
    """Testes para validar que screenshot é a primeira imagem no payload."""

    def test_screenshot_is_first_image_in_payload(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Screenshot deve ser a primeira imagem na lista de imagens."""
        payload = builder_all_images.build_prompt(screenshot_file)

        assert len(payload.images) >= 1
        first_image_bytes, first_label = payload.images[0]
        assert first_label == "screenshot_under_analysis"

    def test_screenshot_bytes_match_file_content(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Bytes do screenshot no payload correspondem ao arquivo original."""
        expected_bytes = screenshot_file.read_bytes()
        payload = builder_all_images.build_prompt(screenshot_file)

        first_image_bytes, _ = payload.images[0]
        assert first_image_bytes == expected_bytes

    def test_screenshot_label_is_correct(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Label do screenshot deve ser 'screenshot_under_analysis'."""
        payload = builder_all_images.build_prompt(screenshot_file)

        _, label = payload.images[0]
        assert label == "screenshot_under_analysis"


class TestReferenceImageLabels:
    """Testes para validar labels corretos para cada imagem de referência."""

    def test_approved_art_reference_label(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Artes_aprovadas_referencia.PNG deve ter label 'approved_art_reference'."""
        payload = builder_all_images.build_prompt(screenshot_file)

        labels = [label for _, label in payload.images]
        assert "approved_art_reference" in labels

    def test_correct_logo_reference_label(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Logo_errado_logo_correto.PNG deve ter label 'correct_logo_reference'."""
        payload = builder_all_images.build_prompt(screenshot_file)

        labels = [label for _, label in payload.images]
        assert "correct_logo_reference" in labels

    def test_official_sky_plus_logo_label(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """logo_sky_plus_amazon.PNG deve ter label 'official_sky_plus_logo'."""
        payload = builder_all_images.build_prompt(screenshot_file)

        labels = [label for _, label in payload.images]
        assert "official_sky_plus_logo" in labels


class TestAllImagesPresent:
    """Testes para cenário com todas as imagens de referência presentes."""

    def test_payload_contains_4_images_total(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Com todas as referências, payload deve ter 4 imagens (1 screenshot + 3 refs)."""
        payload = builder_all_images.build_prompt(screenshot_file)

        assert len(payload.images) == 4

    def test_reference_images_follow_screenshot(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Imagens de referência devem vir após o screenshot."""
        payload = builder_all_images.build_prompt(screenshot_file)

        # Primeiro é screenshot, demais são referências
        _, first_label = payload.images[0]
        assert first_label == "screenshot_under_analysis"

        reference_labels = {label for _, label in payload.images[1:]}
        expected_labels = {
            "approved_art_reference",
            "correct_logo_reference",
            "official_sky_plus_logo",
        }
        assert reference_labels == expected_labels

    def test_all_images_have_non_empty_bytes(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Todas as imagens no payload devem ter bytes não-vazios."""
        payload = builder_all_images.build_prompt(screenshot_file)

        for image_bytes, label in payload.images:
            assert len(image_bytes) > 0, f"Imagem '{label}' tem bytes vazios"

    def test_payload_has_non_empty_prompt_text(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt text deve ser não-vazio quando todas as imagens estão presentes."""
        payload = builder_all_images.build_prompt(screenshot_file)

        assert len(payload.prompt_text) > 0


class TestNoReferenceImages:
    """Testes para cenário sem nenhuma imagem de referência presente."""

    def test_payload_contains_only_screenshot(
        self,
        builder_no_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Sem referências, payload deve ter apenas 1 imagem (screenshot)."""
        payload = builder_no_images.build_prompt(screenshot_file)

        assert len(payload.images) == 1

    def test_screenshot_is_present_without_references(
        self,
        builder_no_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Screenshot deve estar presente mesmo sem imagens de referência."""
        payload = builder_no_images.build_prompt(screenshot_file)

        _, label = payload.images[0]
        assert label == "screenshot_under_analysis"

    def test_rules_text_still_complete_without_references(
        self,
        builder_no_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Texto de regras deve conter todas as 5 seções mesmo sem referências."""
        payload = builder_no_images.build_prompt(screenshot_file)

        assert "### 1. FACILITATOR_ROLE" in payload.prompt_text
        assert "### 2. LOGO_APPLICATION" in payload.prompt_text
        assert "### 3. CONTENT_SEPARATION" in payload.prompt_text
        assert "### 4. NAMING_PRICING" in payload.prompt_text
        assert "### 5. KV_INTEGRITY" in payload.prompt_text

    def test_payload_is_valid_without_references(
        self,
        builder_no_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """PromptPayload deve ser instância válida mesmo sem referências."""
        payload = builder_no_images.build_prompt(screenshot_file)

        assert isinstance(payload, PromptPayload)
        assert isinstance(payload.images, list)
        assert isinstance(payload.prompt_text, str)


class TestScreenshotErrors:
    """Testes para erros de leitura de screenshot."""

    def test_nonexistent_screenshot_raises_error(
        self,
        builder_all_images: CompliancePromptBuilder,
        tmp_path: Path,
    ) -> None:
        """Screenshot inexistente deve levantar AnalysisIncompleteError."""
        fake_path = tmp_path / "nonexistent.png"

        with pytest.raises(AnalysisIncompleteError):
            builder_all_images.build_prompt(fake_path)

    def test_empty_screenshot_raises_error(
        self,
        builder_all_images: CompliancePromptBuilder,
        tmp_path: Path,
    ) -> None:
        """Screenshot com arquivo vazio deve levantar AnalysisIncompleteError."""
        empty_screenshot = tmp_path / "empty.png"
        empty_screenshot.write_bytes(b"")

        with pytest.raises(AnalysisIncompleteError):
            builder_all_images.build_prompt(empty_screenshot)


# --- Testes para brand DGO ---


@pytest.fixture
def dgo_reference_images(base_path: Path) -> Path:
    """Cria imagens de referência DGO no diretório."""
    images_dir = (
        base_path / "watchdog_rules" / "SKY_Amazon_Imagens"
    )

    # Artes aprovadas (compartilhada entre brands)
    (images_dir / "Artes_aprovadas_referencia.PNG").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"artes_aprovadas_content"
    )
    # Logo errado/correto DGO
    (
        images_dir / "Logo_errado_logo_correto_DGO.PNG"
    ).write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"logo_errado_correto_dgo"
    )
    # Logo oficial DGO
    (images_dir / "logo_DGO_amazon.PNG").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"dgo_amazon_content"
    )

    return base_path


@pytest.fixture
def builder_dgo(dgo_reference_images: Path) -> CompliancePromptBuilder:
    """Builder configurado para brand DGO com imagens disponíveis."""
    return CompliancePromptBuilder(
        rules_base_path=dgo_reference_images, brand="dgo"
    )


@pytest.fixture
def builder_dgo_no_images(base_path: Path) -> CompliancePromptBuilder:
    """Builder DGO sem nenhuma imagem de referência."""
    return CompliancePromptBuilder(
        rules_base_path=base_path, brand="dgo"
    )


class TestDGORulesSections:
    """Testes para validar que o prompt DGO contém texto em espanhol."""

    def test_dgo_prompt_contains_spanish_system_prompt(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt DGO deve conter texto em espanhol."""
        payload = builder_dgo.build_prompt(screenshot_file)

        assert "Eres un analista de compliance visual" in (
            payload.prompt_text
        )
        assert "DGO / Amazon Prime" in payload.prompt_text

    def test_dgo_prompt_contains_spanish_language_indicator(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt DGO deve indicar idioma espanhol."""
        payload = builder_dgo.build_prompt(screenshot_file)

        assert "Español (Latinoamérica)" in payload.prompt_text

    def test_dgo_prompt_contains_5_rule_sections(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt DGO deve conter exatamente 5 seções de regras."""
        payload = builder_dgo.build_prompt(screenshot_file)

        assert "### 1. FACILITATOR_ROLE" in payload.prompt_text
        assert "### 2. LOGO_APPLICATION" in payload.prompt_text
        assert "### 3. CONTENT_SEPARATION" in payload.prompt_text
        assert "### 4. NAMING_PRICING" in payload.prompt_text
        assert "### 5. KV_INTEGRITY" in payload.prompt_text

    def test_dgo_prompt_uses_dgo_brand_name(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt DGO deve referenciar DGO nas regras."""
        payload = builder_dgo.build_prompt(screenshot_file)

        assert "DGO" in payload.prompt_text
        # Não deve conter referências SKY+ no corpo das regras
        # (pode conter na seção de formato que é compartilhada)
        assert "Rol de Facilitador DGO" in payload.prompt_text

    def test_dgo_facilitator_role_spanish_references(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Regra facilitator_role DGO deve ter referências em espanhol."""
        payload = builder_dgo.build_prompt(screenshot_file)

        assert "a través de DGO" in payload.prompt_text
        assert "vía DGO" in payload.prompt_text
        assert "DGO con Amazon Prime incluido" in (
            payload.prompt_text
        )

    def test_dgo_naming_pricing_spanish_terms(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Regra naming_pricing DGO deve usar termos em espanhol."""
        payload = builder_dgo.build_prompt(screenshot_file)

        # Nome correto do app em espanhol
        assert "DGO con Amazon Prime incluido" in (
            payload.prompt_text
        )
        # Termos proibidos em espanhol
        assert '"gratis"' in payload.prompt_text
        assert '"sin costo"' in payload.prompt_text
        assert '"a costo cero"' in payload.prompt_text

    def test_dgo_prompt_contains_all_6_rule_ids(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt DGO deve listar as 6 rule_ids no formato de resposta."""
        payload = builder_dgo.build_prompt(screenshot_file)

        assert "facilitator_role" in payload.prompt_text
        assert "logo_application" in payload.prompt_text
        assert "logo_effects" in payload.prompt_text
        assert "content_separation" in payload.prompt_text
        assert "naming_pricing" in payload.prompt_text
        assert "kv_integrity" in payload.prompt_text


class TestDGOReferenceImages:
    """Testes para validar carregamento de imagens DGO."""

    def test_dgo_loads_correct_reference_images(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """DGO deve carregar imagens específicas do brand."""
        payload = builder_dgo.build_prompt(screenshot_file)

        labels = [label for _, label in payload.images]
        assert "approved_art_reference" in labels
        assert "correct_logo_reference" in labels
        assert "official_brand_logo" in labels

    def test_dgo_payload_contains_4_images_total(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Com todas as referências DGO, payload deve ter 4 imagens."""
        payload = builder_dgo.build_prompt(screenshot_file)

        assert len(payload.images) == 4

    def test_dgo_screenshot_is_first_image(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Screenshot deve ser primeira imagem mesmo para DGO."""
        payload = builder_dgo.build_prompt(screenshot_file)

        _, label = payload.images[0]
        assert label == "screenshot_under_analysis"

    def test_dgo_does_not_load_sky_plus_logo(
        self,
        builder_dgo: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """DGO não deve carregar o logo SKY+ específico."""
        payload = builder_dgo.build_prompt(screenshot_file)

        labels = [label for _, label in payload.images]
        assert "official_sky_plus_logo" not in labels

    def test_dgo_no_images_still_works(
        self,
        builder_dgo_no_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """DGO sem referências deve funcionar com apenas screenshot."""
        payload = builder_dgo_no_images.build_prompt(
            screenshot_file
        )

        assert len(payload.images) == 1
        _, label = payload.images[0]
        assert label == "screenshot_under_analysis"

    def test_dgo_no_images_still_has_complete_rules(
        self,
        builder_dgo_no_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """DGO sem referências ainda deve ter regras completas."""
        payload = builder_dgo_no_images.build_prompt(
            screenshot_file
        )

        assert "### 1. FACILITATOR_ROLE" in payload.prompt_text
        assert "### 5. KV_INTEGRITY" in payload.prompt_text
        assert "DGO" in payload.prompt_text


class TestSkyPlusBackwardCompatibility:
    """Testes de backward compat — SKY+ default continua funcionando."""

    def test_default_brand_is_sky_plus(
        self,
        all_reference_images: Path,
        screenshot_file: Path,
    ) -> None:
        """Builder sem brand explícito deve usar sky_plus."""
        builder = CompliancePromptBuilder(
            rules_base_path=all_reference_images
        )
        payload = builder.build_prompt(screenshot_file)

        assert "SKY+" in payload.prompt_text
        assert "Português (Brasil)" in payload.prompt_text

    def test_explicit_sky_plus_brand_works(
        self,
        all_reference_images: Path,
        screenshot_file: Path,
    ) -> None:
        """Builder com brand='sky_plus' deve funcionar igual ao default."""
        builder = CompliancePromptBuilder(
            rules_base_path=all_reference_images,
            brand="sky_plus",
        )
        payload = builder.build_prompt(screenshot_file)

        assert "SKY+" in payload.prompt_text
        labels = [label for _, label in payload.images]
        assert "official_sky_plus_logo" in labels

    def test_sky_plus_prompt_is_portuguese(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt SKY+ deve conter indicador de idioma português."""
        payload = builder_all_images.build_prompt(screenshot_file)

        assert "Português (Brasil)" in payload.prompt_text

    def test_sky_plus_contains_brazilian_prohibited_terms(
        self,
        builder_all_images: CompliancePromptBuilder,
        screenshot_file: Path,
    ) -> None:
        """Prompt SKY+ deve ter termos proibidos em português."""
        payload = builder_all_images.build_prompt(screenshot_file)

        assert '"grátis"' in payload.prompt_text
        assert '"de graça"' in payload.prompt_text
        assert '"sem custo"' in payload.prompt_text
