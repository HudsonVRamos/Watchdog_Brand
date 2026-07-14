"""Builder de prompt multimodal para análise de compliance.

Constrói o payload completo para envio ao Bedrock, contendo:
- Screenshot do ISP sob análise (obrigatório)
- Imagens de referência oficiais (opcional, graceful degradation)
- Texto com as 5 seções de regras de compliance

Suporta múltiplas marcas (SKY+ e DGO) com regras adaptadas
por idioma e terminologia de cada brand.

Requisitos cobertos: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from brand_watchdog.analyzer.compliance_exceptions import (
    AnalysisIncompleteError,
)

logger = logging.getLogger(__name__)


@dataclass
class PromptPayload:
    """Payload pronto para envio ao BedrockClient.

    Attributes:
        images: Lista de tuplas (image_bytes, label) na ordem:
            [screenshot, ...reference_images] (build_prompt) ou
            [...reference_images, screenshot] (build_prompt_cached).
        prompt_text: Texto completo do prompt com regras de compliance.
        cache_control_index: Índice do último bloco estático na
            lista de imagens (para marcação cache_control ephemeral).
            None quando Prompt Caching não está habilitado.
        media_types: Lista de media_types correspondente a cada
            imagem em images. None quando não especificado
            (backward compatibility com build_prompt).
    """

    images: list[tuple[bytes, str]]  # (image_bytes, label)
    prompt_text: str
    cache_control_index: int | None = None
    media_types: list[str] | None = None


# Mapeamento de imagens de referência por brand
BRAND_REFERENCE_IMAGES: dict[str, dict[str, str]] = {
    "sky_plus": {
        "Artes_aprovadas_referencia.PNG": "approved_art_reference",
        "Logo_errado_logo_correto.PNG": "correct_logo_reference",
        "logo_sky_plus_amazon.PNG": "official_sky_plus_logo",
    },
    "dgo": {
        "Artes_aprovadas_referencia.PNG": "approved_art_reference",
        "Logo_errado_logo_correto_DGO.PNG": "correct_logo_reference",
        "logo_DGO_amazon.PNG": "official_brand_logo",
    },
}


class CompliancePromptBuilder:
    """Construtor de prompts multimodais para análise de compliance.

    Carrega imagens de referência do diretório configurado e monta
    o prompt com as 5 seções de regras de compliance adaptadas
    ao brand configurado (SKY+ ou DGO).
    """

    REFERENCE_IMAGES_DIR: Path = Path(
        "watchdog_rules/SKY_Amazon_Imagens"
    )

    # Mantido para backward compatibility com testes existentes
    REFERENCE_IMAGES: dict[str, str] = {
        "Artes_aprovadas_referencia.PNG": "approved_art_reference",
        "Logo_errado_logo_correto.PNG": "correct_logo_reference",
        "logo_sky_plus_amazon.PNG": "official_sky_plus_logo",
    }

    def __init__(
        self,
        rules_base_path: Path | None = None,
        brand: str = "sky_plus",
    ) -> None:
        """Inicializa o builder com o caminho base para regras.

        Args:
            rules_base_path: Caminho base do projeto para localizar
                o diretório de imagens de referência. Se None, usa
                o diretório de trabalho atual.
            brand: Tipo de brand para monitoramento.
                "sky_plus" (padrão) ou "dgo".
        """
        self._brand = brand

        if rules_base_path is not None:
            self._images_dir = (
                rules_base_path / self.REFERENCE_IMAGES_DIR
            )
        else:
            self._images_dir = self.REFERENCE_IMAGES_DIR

    def build_prompt(self, screenshot_path: Path) -> PromptPayload:
        """Constrói o payload completo para análise de compliance.

        O payload contém o screenshot como imagem primária, seguido
        das imagens de referência disponíveis, e o texto com todas
        as regras de compliance.

        Args:
            screenshot_path: Caminho para o screenshot do ISP a analisar.

        Returns:
            PromptPayload com imagens e texto do prompt.

        Raises:
            AnalysisIncompleteError: Se o screenshot não pode ser lido.
        """
        # Carregar screenshot (obrigatório)
        screenshot_bytes = self._load_screenshot(screenshot_path)

        # Montar lista de imagens: screenshot primeiro
        images: list[tuple[bytes, str]] = [
            (screenshot_bytes, "screenshot_under_analysis"),
        ]

        # Carregar imagens de referência (opcional)
        reference_images = self._load_reference_images()
        images.extend(reference_images)

        # Construir texto de regras
        prompt_text = self._build_rules_text()

        return PromptPayload(images=images, prompt_text=prompt_text)

    def build_prompt_cached(
        self,
        screenshot_bytes: bytes,
        reference_images: list[tuple[bytes, str]] | None = None,
    ) -> PromptPayload:
        """Constrói payload otimizado para Prompt Caching do Bedrock.

        Organiza o payload com conteúdo estático (regras + imagens de
        referência) ANTES do conteúdo variável (screenshot), permitindo
        que o Bedrock reutilize o prefixo entre chamadas consecutivas.

        As imagens de referência são recebidas já em bytes (JPEG, do
        ReferenceImageCache) e o screenshot é recebido diretamente
        como bytes (PNG).

        Args:
            screenshot_bytes: Bytes do screenshot (formato PNG).
            reference_images: Lista de tuplas (image_bytes, label)
                com imagens de referência já processadas pelo
                ReferenceImageCache (formato JPEG). Se None ou
                vazia, o payload contém apenas o screenshot.

        Returns:
            PromptPayload com imagens ordenadas para Prompt Caching:
            - Imagens de referência (estáticas, JPEG) primeiro
            - Screenshot (variável, PNG) por último
            - cache_control_index apontando para o último bloco
              estático (última referência)
            - media_types indicando o formato de cada imagem

        Raises:
            AnalysisIncompleteError: Se screenshot_bytes estiver vazio.
        """
        if not screenshot_bytes:
            raise AnalysisIncompleteError(
                "Screenshot vazio ou ilegível (bytes vazios)"
            )

        # Montar lista de imagens: ESTÁTICAS primeiro, VARIÁVEL depois
        images: list[tuple[bytes, str]] = []
        media_types: list[str] = []

        # Referências primeiro (estáticas, JPEG)
        if reference_images:
            for ref_bytes, ref_label in reference_images:
                images.append((ref_bytes, ref_label))
                media_types.append("image/jpeg")

        # Screenshot por último (variável, PNG)
        images.append(
            (screenshot_bytes, "screenshot_under_analysis")
        )
        media_types.append("image/png")

        # Determinar índice do último bloco estático
        # (última imagem de referência, antes do screenshot)
        cache_control_index: int | None = None
        if reference_images:
            cache_control_index = len(reference_images) - 1

        # Construir texto de regras
        prompt_text = self._build_rules_text()

        return PromptPayload(
            images=images,
            prompt_text=prompt_text,
            cache_control_index=cache_control_index,
            media_types=media_types,
        )

    def _load_screenshot(self, screenshot_path: Path) -> bytes:
        """Carrega os bytes do screenshot para análise.

        Args:
            screenshot_path: Caminho para o arquivo de screenshot.

        Returns:
            Bytes do arquivo de screenshot.

        Raises:
            AnalysisIncompleteError: Se o screenshot não existe ou
                não pode ser lido.
        """
        try:
            data = screenshot_path.read_bytes()
            if not data:
                raise AnalysisIncompleteError(
                    f"Screenshot vazio ou ilegível: {screenshot_path}"
                )
            return data
        except OSError as e:
            raise AnalysisIncompleteError(
                f"Não foi possível ler o screenshot '{screenshot_path}': {e}"
            ) from e

    def _get_reference_images_map(self) -> dict[str, str]:
        """Retorna o mapeamento de imagens para o brand atual.

        Returns:
            Dicionário {filename: label} para o brand configurado.
            Fallback para REFERENCE_IMAGES (sky_plus) se brand
            desconhecido.
        """
        return BRAND_REFERENCE_IMAGES.get(
            self._brand, BRAND_REFERENCE_IMAGES["sky_plus"]
        )

    def _load_reference_images(self) -> list[tuple[bytes, str]]:
        """Carrega as imagens de referência disponíveis.

        Para cada imagem configurada no mapeamento do brand atual,
        tenta carregar os bytes do arquivo. Se o arquivo não existe
        ou não pode ser lido, registra um warning e continua com
        as demais imagens.

        Returns:
            Lista de tuplas (image_bytes, label) para imagens
            carregadas com sucesso. Pode ser vazia se nenhuma
            imagem está disponível.
        """
        loaded_images: list[tuple[bytes, str]] = []
        reference_map = self._get_reference_images_map()

        for filename, label in reference_map.items():
            image_path = self._images_dir / filename

            try:
                data = image_path.read_bytes()
                if not data:
                    logger.warning(
                        "Imagem de referência vazia ou "
                        "ilegível: %s",
                        filename,
                    )
                    continue
                loaded_images.append((data, label))
            except OSError as e:
                logger.warning(
                    "Imagem de referência ausente ou "
                    "ilegível: %s. Motivo: %s",
                    filename,
                    e,
                )

        return loaded_images

    def _build_rules_text(self) -> str:
        """Constrói o texto completo das regras de compliance.

        O texto contém exatamente 5 seções de regras adaptadas
        ao brand configurado (idioma e terminologia).

        O prompt instrui o modelo a responder em JSON com
        "compliance_results" contendo todas as 6 regras
        (incluindo logo_effects separado de logo_application).

        Returns:
            Texto formatado com todas as regras de compliance.
        """
        if self._brand == "dgo":
            return self._build_rules_text_dgo()
        return self._build_rules_text_sky_plus()

    def _build_rules_text_sky_plus(self) -> str:
        """Constrói regras de compliance para SKY+ (português)."""
        sections = [
            self._section_facilitator_role(),
            self._section_logo_application(),
            self._section_content_separation(),
            self._section_naming_pricing(),
            self._section_kv_integrity(),
        ]

        rules_text = (
            "Você é um analista de compliance visual "
            "especializado na parceria SKY+ / Amazon Prime. "
            "O conteúdo do website está em Português (Brasil). "
            "Analise o screenshot fornecido e valide cada uma "
            "das regras de compliance abaixo, comparando com "
            "as imagens de referência quando disponíveis.\n\n"
            "## REGRAS DE COMPLIANCE\n\n"
        )

        rules_text += "\n\n".join(sections)
        rules_text += self._response_format_section()

        return rules_text

    def _build_rules_text_dgo(self) -> str:
        """Constrói regras de compliance para DGO (espanhol)."""
        sections = [
            self._section_facilitator_role_dgo(),
            self._section_logo_application_dgo(),
            self._section_content_separation_dgo(),
            self._section_naming_pricing_dgo(),
            self._section_kv_integrity_dgo(),
        ]

        rules_text = (
            "Eres un analista de compliance visual "
            "especializado en la asociación DGO / Amazon "
            "Prime. El contenido del sitio web está en "
            "Español (Latinoamérica). Analiza el screenshot "
            "proporcionado y valida cada una de las reglas "
            "de compliance a continuación, comparando con "
            "las imágenes de referencia cuando estén "
            "disponibles.\n\n"
            "## REGLAS DE COMPLIANCE\n\n"
        )

        rules_text += "\n\n".join(sections)
        rules_text += self._response_format_section()

        return rules_text

    def _response_format_section(self) -> str:
        """Seção de formato de resposta JSON (igual para ambos brands)."""
        return (
            "\n\n## REGRA CRÍTICA: PRECISÃO E CONSERVADORISMO\n\n"
            "### 1. NÃO ALUCINAR\n"
            "ANTES de avaliar qualquer regra, verifique se o screenshot "
            "contém ALGUMA menção visível e clara a:\n"
            "- Amazon Prime, Prime Video, Amazon Music, Prime Gaming, Prime Reading\n"
            "- SKY+ (ou DGO)\n"
            "- Logos da parceria SKY+/Amazon (ou DGO/Amazon)\n\n"
            "Se NÃO encontrar NENHUMA dessas menções no screenshot, "
            "TODAS as 6 regras devem ser NOT_APPLICABLE com a descrição: "
            "'Nenhum conteúdo relacionado à parceria SKY+/Amazon Prime "
            "foi encontrado no screenshot.'\n\n"
            "### 2. SER CONSERVADOR NOS FAILS\n"
            "### 2. CONSERVADOR PARA DETECTAR, ASSERTIVO PARA JULGAR\n"
            "- CONSERVADOR PARA DETECTAR: Não invente conteúdo. Só avalie "
            "regras se você REALMENTE vê o elemento no screenshot.\n"
            "- ASSERTIVO PARA JULGAR: Quando ENCONTRAR evidência real de "
            "violação, marque FAIL SEM HESITAR. Não seja 'bonzinho'.\n\n"
            "EXEMPLOS DE QUANDO DEVE SER FAIL OBRIGATÓRIO:\n"
            "- Preço de combo SKY+/Amazon abaixo de R$80,00 → FAIL naming_pricing\n"
            "- Prime Video ou Amazon Prime vendido SEPARADO do SKY+ → FAIL facilitator_role\n"
            "- Amazon Prime na mesma página que Disney+, Telecine, GloboPlay → FAIL kv_integrity\n"
            "- Uso de 'Prime Video' ou 'SKY' isolado em vez de 'SKY+ com Amazon Prime incluso' → FAIL naming_pricing\n"
            "- Arte da parceria em página pública de ISP → FAIL content_separation\n\n"
            "Regras de rigor:\n"
            "- Deve apontar EXATAMENTE o que viu no screenshot (texto literal, "
            "posição na página, elemento visual específico).\n"
            "- NÃO confundir nome do PLANO DE INTERNET do ISP (ex: 'Combo Prime', "
            "'Smart 500MB') com o nome do SERVIÇO Amazon. O nome do plano do ISP "
            "pode ser qualquer coisa. A regra naming_pricing se aplica quando o "
            "site menciona Amazon Prime ou SKY+ — nesses casos DEVE usar a "
            "nomenclatura 'SKY+ com Amazon Prime incluso'.\n"
            "- NÃO marcar logo_effects como FAIL apenas por logos estarem "
            "em tamanho pequeno ou sobre fundo colorido. FAIL é apenas para "
            "efeitos CLARAMENTE indevidos: filtros de cor, sombras grossas, "
            "distorção, ou brilho aplicado SOBRE o logo.\n\n"
            "### 3. FORMATO DE RESPOSTA\n\n"
            "Responda EXCLUSIVAMENTE em JSON válido, sem "
            "markdown, com a seguinte estrutura:\n\n"
            "```json\n"
            "{\n"
            '  "compliance_results": [\n'
            "    {\n"
            '      "rule_id": "<rule_identifier>",\n'
            '      "status": "PASS" | "FAIL" | '
            '"NOT_APPLICABLE",\n'
            '      "confidence": <integer 0-100>,\n'
            '      "description": "<descrição PRECISA dos achados, '
            "citando texto literal ou elementos visuais ESPECÍFICOS "
            'vistos no screenshot, em até 1024 caracteres>"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "```\n\n"
            "Inclua resultados para TODAS as 6 regras a "
            "seguir, nesta ordem:\n"
            "1. facilitator_role\n"
            "2. logo_application\n"
            "3. logo_effects\n"
            "4. content_separation\n"
            "5. naming_pricing\n"
            "6. kv_integrity\n\n"
            "Use NOT_APPLICABLE quando a regra não se "
            "aplica ao conteúdo visível no screenshot.\n\n"
            "Na descrição, SEMPRE indique:\n"
            "- Se PASS: O QUE foi encontrado e por que está correto.\n"
            "- Se NOT_APPLICABLE: Confirme que nenhum material da parceria "
            "foi detectado no screenshot.\n"
            "- Se FAIL: Cite o TEXTO LITERAL ou ELEMENTO VISUAL EXATO "
            "que constitui a violação, com localização na página."
        )

    def _section_facilitator_role(self) -> str:
        """Seção de regras para facilitator_role."""
        return (
            "### 1. FACILITATOR_ROLE (Papel de Facilitador SKY+)\n\n"
            "Verifique se toda menção a serviços Amazon Prime "
            "(Amazon Prime, Prime Video, Amazon Music, Prime Gaming, "
            "Prime Reading) está associada na mesma página a uma referência "
            "ao SKY+ como facilitador.\n\n"
            "Referências válidas ao SKY+ como facilitador:\n"
            '- "SKY+"\n'
            '- "através do SKY+"\n'
            '- "via SKY+"\n'
            '- "SKY+ com Amazon Prime incluso"\n'
            "- Logo SKY+ com Amazon Prime (veja imagem de referência)\n\n"
            "FAIL: Se qualquer menção a Amazon Prime aparece sem referência "
            "ao SKY+ na mesma página.\n"
            "PASS: Se todas as menções estão associadas ao SKY+.\n"
            "NOT_APPLICABLE: Se não há menção a serviços Amazon Prime."
        )

    def _section_logo_application(self) -> str:
        """Seção de regras para logo_application e logo_effects."""
        return (
            "### 2. LOGO_APPLICATION (Aplicação de Logos) e LOGO_EFFECTS "
            "(Efeitos em Logos)\n\n"
            "**Regra logo_application:**\n"
            "Verifique a aplicação correta dos logos da parceria:\n"
            "- O logo SKY+ com Amazon Prime deve aparecer PRIMEIRO na ordem "
            "de leitura (esquerda para direita), antes de logos de serviços "
            "Prime individuais (Amazon Music, Prime Gaming, Prime Reading).\n"
            "- Logos devem ser separados por barra vertical com espaçamento.\n"
            "- Logos NÃO podem estar: dentro de frases, com proporções "
            "alteradas, com cores alteradas, inclinados, sobre fundos "
            "estampados ou com baixa legibilidade.\n\n"
            "FAIL logo_application: Se a ordem está incorreta, separadores "
            "faltam, ou logos estão aplicados incorretamente.\n"
            "PASS logo_application: Se todas as regras de aplicação estão "
            "corretas.\n\n"
            "**Regra logo_effects:**\n"
            "Verifique se há efeitos visuais indevidos sobre os logos:\n"
            "- Efeitos de luz, sombras ou filtros sobrepostos ou adjacentes "
            "ao logo.\n"
            "- Cor do logo alterada em relação às cores oficiais (compare "
            "com a imagem de referência do logo correto).\n\n"
            "FAIL logo_effects: Se efeitos visuais estão aplicados sobre "
            "ou adjacentes ao logo, ou se cores foram alteradas.\n"
            "PASS logo_effects: Se nenhum efeito indevido é detectado.\n"
            "NOT_APPLICABLE: Se nenhum logo da parceria SKY+/Amazon é "
            "detectado no screenshot."
        )

    def _section_content_separation(self) -> str:
        """Seção de regras para content_separation."""
        return (
            "### 3. CONTENT_SEPARATION (Separação Visual de Conteúdo)\n\n"
            "Verifique se o conteúdo do parceiro (identidade visual, "
            "tipografia, imagens, preços, ofertas, mensagens publicitárias) "
            "está visualmente separado da arte SKY+/Amazon.\n\n"
            "Métodos de separação aceitos:\n"
            "- Blocos/seções distintas\n"
            "- Mockups de dispositivos\n"
            "- Elemento gráfico snake do SKY+\n\n"
            "FAIL: Se elementos se sobrepõem ou são colocados sobre logos "
            "ou conteúdo KV sem fronteira visual clara. Também FAIL se "
            "arte da parceria SKY+/Amazon aparece em página pública de ISP "
            "(artes da parceria requerem aprovação prévia da Amazon para "
            "uso em websites).\n"
            "PASS: Se conteúdo do parceiro está adequadamente separado, "
            "ou se não há conteúdo de parceiro na página."
        )

    def _section_naming_pricing(self) -> str:
        """Seção de regras para naming_pricing."""
        return (
            "### 4. NAMING_PRICING (Nomenclatura e Preços)\n\n"
            "Verifique as regras de nomenclatura e preços:\n\n"
            "**Nome do app:** O nome correto é "
            '"SKY+ com Amazon Prime incluso" '
            "(comparação case-insensitive).\n"
            "FAIL se qualquer variação diferente for encontrada.\n\n"
            "**Preço mínimo:** O preço do combo SKY+/Amazon Prime NÃO "
            "pode ser inferior a R$80,00.\n"
            "FAIL se valor abaixo de R$80,00 for detectado.\n\n"
            "**Termos proibidos:** Os seguintes termos NÃO podem ser "
            "usados no contexto da parceria SKY+/Amazon Prime:\n"
            '- "grátis"\n'
            '- "gratuito"\n'
            '- "de graça"\n'
            '- "sem custo" / "sem custos"\n'
            '- "a custo zero"\n'
            '- "100% grátis"\n\n'
            "FAIL: Se nome incorreto, preço abaixo de R$80,00, ou termo "
            "proibido for detectado.\n"
            "PASS: Se todas as regras de nomenclatura e preço estão corretas.\n"
            "NOT_APPLICABLE: Se não há menção a nomes ou preços da parceria."
        )

    def _section_kv_integrity(self) -> str:
        """Seção de regras para kv_integrity."""
        return (
            "### 5. KV_INTEGRITY (Integridade do Key Visual)\n\n"
            "Verifique a integridade dos materiais visuais oficiais (KV) "
            "da campanha, comparando com as imagens de referência:\n\n"
            "- O KV oficial NÃO pode ser alterado em corte, cor, posição "
            "ou efeitos (compare com a imagem de referência de artes "
            "aprovadas).\n"
            "- Logos de parceiros NÃO podem ser colocados sobre ou "
            "sobrepondo conteúdo KV.\n"
            "- O logo SKY+ NÃO pode estar: espelhado horizontalmente, "
            "invertido verticalmente, rotacionado, ou fora da área "
            "designada.\n"
            "- Filtros, sombras ou efeitos visuais NÃO podem ser aplicados "
            "ao logo SKY+ dentro do KV.\n"
            "- Comunicação da parceria Amazon NÃO pode aparecer na mesma "
            "página que comunicações de outros parceiros do ISP "
            "(violação de exclusividade).\n\n"
            "FAIL: Se qualquer alteração, sobreposição, distorção, efeito "
            "ou violação de exclusividade for detectada.\n"
            "PASS: Se todas as regras de integridade do KV estão corretas.\n"
            "NOT_APPLICABLE: Se nenhum elemento KV é detectado no screenshot."
        )

    # --- Seções DGO (Espanhol - América Latina) ---

    def _section_facilitator_role_dgo(self) -> str:
        """Seção de regras para facilitator_role (DGO)."""
        return (
            "### 1. FACILITATOR_ROLE (Rol de Facilitador DGO)\n\n"
            "Verifica que toda mención a servicios Amazon Prime "
            "(Amazon Prime, Prime Video, Amazon Music, Prime "
            "Gaming, Prime Reading) esté asociada en la misma "
            "página a una referencia a DGO como facilitador.\n\n"
            "Referencias válidas a DGO como facilitador:\n"
            '- "DGO"\n'
            '- "a través de DGO"\n'
            '- "vía DGO"\n'
            '- "DGO con Amazon Prime incluido"\n'
            "- Logo DGO con Amazon Prime (ver imagen de "
            "referencia)\n\n"
            "FAIL: Si cualquier mención a Amazon Prime aparece "
            "sin referencia a DGO en la misma página.\n"
            "PASS: Si todas las menciones están asociadas a "
            "DGO.\n"
            "NOT_APPLICABLE: Si no hay mención a servicios "
            "Amazon Prime."
        )

    def _section_logo_application_dgo(self) -> str:
        """Seção de regras para logo_application e logo_effects (DGO)."""
        return (
            "### 2. LOGO_APPLICATION (Aplicación de Logos) y "
            "LOGO_EFFECTS (Efectos en Logos)\n\n"
            "**Regla logo_application:**\n"
            "Verifica la aplicación correcta de los logos de "
            "la asociación:\n"
            "- El logo DGO con Amazon Prime debe aparecer "
            "PRIMERO en el orden de lectura (izquierda a "
            "derecha), antes de logos de servicios Prime "
            "individuales (Amazon Music, Prime Gaming, Prime "
            "Reading).\n"
            "- Los logos deben estar separados por barra "
            "vertical con espaciado.\n"
            "- Los logos NO pueden estar: dentro de frases, "
            "con proporciones alteradas, con colores alterados, "
            "inclinados, sobre fondos estampados o con baja "
            "legibilidad.\n\n"
            "FAIL logo_application: Si el orden es incorrecto, "
            "faltan separadores, o los logos están aplicados "
            "incorrectamente.\n"
            "PASS logo_application: Si todas las reglas de "
            "aplicación son correctas.\n\n"
            "**Regla logo_effects:**\n"
            "Verifica si hay efectos visuales indebidos sobre "
            "los logos:\n"
            "- Efectos de luz, sombras o filtros superpuestos "
            "o adyacentes al logo.\n"
            "- Color del logo alterado respecto a los colores "
            "oficiales (comparar con la imagen de referencia "
            "del logo correcto).\n\n"
            "FAIL logo_effects: Si efectos visuales están "
            "aplicados sobre o adyacentes al logo, o si los "
            "colores fueron alterados.\n"
            "PASS logo_effects: Si ningún efecto indebido es "
            "detectado.\n"
            "NOT_APPLICABLE: Si ningún logo de la asociación "
            "DGO/Amazon es detectado en el screenshot."
        )

    def _section_content_separation_dgo(self) -> str:
        """Seção de regras para content_separation (DGO)."""
        return (
            "### 3. CONTENT_SEPARATION (Separación Visual de "
            "Contenido)\n\n"
            "Verifica que el contenido del socio (identidad "
            "visual, tipografía, imágenes, precios, ofertas, "
            "mensajes publicitarios) esté visualmente separado "
            "del arte DGO/Amazon.\n\n"
            "Métodos de separación aceptados:\n"
            "- Bloques/secciones distintas\n"
            "- Mockups de dispositivos\n"
            "- Elemento gráfico distintivo de DGO\n\n"
            "FAIL: Si elementos se superponen o se colocan "
            "sobre logos o contenido KV sin frontera visual "
            "clara. También FAIL si arte de la asociación "
            "DGO/Amazon aparece en página pública de ISP "
            "(artes de la asociación requieren aprobación "
            "previa de Amazon para uso en sitios web).\n"
            "PASS: Si el contenido del socio está adecuadamente "
            "separado, o si no hay contenido de socio en la "
            "página."
        )

    def _section_naming_pricing_dgo(self) -> str:
        """Seção de regras para naming_pricing (DGO)."""
        return (
            "### 4. NAMING_PRICING (Nomenclatura y Precios)\n\n"
            "Verifica las reglas de nomenclatura y precios:\n\n"
            "**Nombre de la app:** El nombre correcto es "
            '"DGO con Amazon Prime incluido" '
            "(comparación case-insensitive).\n"
            "FAIL si cualquier variación diferente es "
            "encontrada.\n\n"
            "**Precio mínimo:** El precio del combo "
            "DGO/Amazon Prime NO puede ser inferior al "
            "mínimo establecido.\n"
            "FAIL si el valor está por debajo del mínimo.\n\n"
            "**Términos prohibidos:** Los siguientes términos "
            "NO pueden ser usados CUANDO SE REFIEREN AL "
            "SERVICIO DGO/Amazon Prime en sí (es decir, "
            "cuando implican que el servicio DGO o Amazon "
            "Prime es gratuito):\n"
            '- "gratis"\n'
            '- "gratuito"\n'
            '- "sin costo" / "sin costos"\n'
            '- "a costo cero"\n'
            '- "100% gratis"\n\n'
            "IMPORTANTE: Si el término 'gratis' o similar "
            "se refiere a PREMIOS, SORTEOS, REGALOS "
            "promocionales u otros beneficios que NO son "
            "el servicio DGO/Amazon en sí (ej: 'llévate "
            "premios GRATIS', 'gana regalos gratis'), "
            "esto NO es una violación. Solo es FAIL si "
            "implica que el SERVICIO DGO o Amazon Prime "
            "es gratuito.\n\n"
            "FAIL: Si nombre incorrecto, precio por debajo "
            "del mínimo, o término prohibido aplicado al "
            "servicio DGO/Amazon es detectado.\n"
            "PASS: Si todas las reglas de nomenclatura y "
            "precio son correctas, o si los términos "
            "prohibidos solo se usan en contexto de premios/"
            "sorteos.\n"
            "NOT_APPLICABLE: Si no hay mención a nombres o "
            "precios de la asociación."
        )

    def _section_kv_integrity_dgo(self) -> str:
        """Seção de regras para kv_integrity (DGO)."""
        return (
            "### 5. KV_INTEGRITY (Integridad del Key "
            "Visual)\n\n"
            "Verifica la integridad de los materiales "
            "visuales oficiales (KV) de la campaña, "
            "comparando con las imágenes de referencia:\n\n"
            "- El KV oficial NO puede ser alterado en corte, "
            "color, posición o efectos (comparar con la "
            "imagen de referencia de artes aprobadas).\n"
            "- Logos de socios EXTERNOS NO pueden ser "
            "colocados sobre o superponiendo contenido KV.\n"
            "- El logo DGO NO puede estar: espejado "
            "horizontalmente, invertido verticalmente, "
            "rotado, o fuera del área designada.\n"
            "- Filtros, sombras o efectos visuales NO pueden "
            "ser aplicados al logo DGO dentro del KV.\n"
            "- Comunicación de la asociación Amazon NO puede "
            "aparecer en la misma página que comunicaciones "
            "de socios EXTERNOS al grupo DGO (violación de "
            "exclusividad).\n\n"
            "IMPORTANTE: DSports, DGO, DPlay y otras marcas "
            "del GRUPO DGO/DIRECTV NO son 'otros socios'. "
            "Son marcas hermanas del mismo grupo empresarial. "
            "Su presencia junto a DGO/Amazon NO es una "
            "violación de exclusividad. La regla de "
            "exclusividad aplica SOLO para marcas "
            "COMPLETAMENTE EXTERNAS al grupo DGO/DIRECTV "
            "(ej: Netflix, Disney+, HBO, etc.).\n\n"
            "FAIL: Si cualquier alteración, superposición, "
            "distorsión, efecto o violación de exclusividad "
            "con marcas EXTERNAS es detectada.\n"
            "PASS: Si todas las reglas de integridad del KV "
            "son correctas.\n"
            "NOT_APPLICABLE: Si ningún elemento KV es "
            "detectado en el screenshot."
        )
