"""Testes de integração para o fluxo de análise.

Valida o pipeline completo: screenshot (real file I/O) → Analyzer
→ prompt building (real brand assets) → response parsing (mock Bedrock)
→ confidence filtering → DetectionResults.

Requirements: 4.1, 4.4, 4.5
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brand_watchdog.analyzer.analyzer import (
    Analyzer,
    AnalysisIncompleteError,
    CONFIRMED_MATCH_THRESHOLD,
)
from brand_watchdog.config import AnalyzerConfig
from brand_watchdog.models.dataclasses import BrandAsset


def _create_minimal_png(width: int = 1, height: int = 1) -> bytes:
    """Cria um PNG válido mínimo (pixel branco).

    Gera um arquivo PNG real com header, IHDR, IDAT e IEND chunks.
    """
    # Assinatura PNG
    signature = b"\x89PNG\r\n\x1a\n"

    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr_chunk = (
        struct.pack(">I", len(ihdr_data))
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", ihdr_crc)
    )

    # IDAT chunk (pixel branco RGB, com filter byte 0)
    raw_data = b"\x00" + b"\xff\xff\xff" * width
    raw_rows = raw_data * height
    compressed = zlib.compress(raw_rows)
    idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
    idat_chunk = (
        struct.pack(">I", len(compressed))
        + b"IDAT"
        + compressed
        + struct.pack(">I", idat_crc)
    )

    # IEND chunk
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend_chunk = (
        struct.pack(">I", 0)
        + b"IEND"
        + struct.pack(">I", iend_crc)
    )

    return signature + ihdr_chunk + idat_chunk + iend_chunk


@pytest.fixture
def real_screenshot(tmp_path: Path) -> Path:
    """Cria um arquivo PNG real no disco para testes de I/O."""
    screenshot_path = tmp_path / "screenshot_test.png"
    png_bytes = _create_minimal_png(width=4, height=4)
    screenshot_path.write_bytes(png_bytes)
    return screenshot_path


@pytest.fixture
def brand_assets_logo_e_texto() -> list[BrandAsset]:
    """Cria objetos BrandAsset reais (logo + texto)."""
    return [
        BrandAsset(
            id="logo-001",
            asset_type="logo",
            file_path=Path("/assets/logos/brand_main.png"),
            text_value=None,
            content_hash="a1b2c3d4e5f6",
            original_filename="brand_main.png",
            file_size_bytes=4096,
            created_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        ),
        BrandAsset(
            id="logo-002",
            asset_type="logo",
            file_path=Path("/assets/logos/brand_icon.svg"),
            text_value=None,
            content_hash="f6e5d4c3b2a1",
            original_filename="brand_icon.svg",
            file_size_bytes=2048,
            created_at=datetime(2024, 1, 15, 10, 5, 0, tzinfo=timezone.utc),
        ),
        BrandAsset(
            id="text-001",
            asset_type="text",
            file_path=None,
            text_value="SkyBrand",
            content_hash="text_hash_001",
            original_filename=None,
            file_size_bytes=None,
            created_at=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
        ),
        BrandAsset(
            id="text-002",
            asset_type="text",
            file_path=None,
            text_value="DGO Digital",
            content_hash="text_hash_002",
            original_filename=None,
            file_size_bytes=None,
            created_at=datetime(2024, 1, 15, 11, 5, 0, tzinfo=timezone.utc),
        ),
    ]


@pytest.fixture
def realistic_bedrock_response() -> dict:
    """Resposta realista do Bedrock com múltiplas detecções."""
    return {
        "detections": [
            {
                "match_type": "logo",
                "confidence": 92,
                "bounding_box": {
                    "x_percent": 2.5,
                    "y_percent": 1.0,
                    "width_percent": 12.0,
                    "height_percent": 8.0,
                },
                "description": (
                    "Logo 'brand_main.png' encontrado no header "
                    "superior esquerdo do site, com tamanho reduzido "
                    "e cores levemente alteradas."
                ),
            },
            {
                "match_type": "text",
                "confidence": 78,
                "bounding_box": {
                    "x_percent": 25.0,
                    "y_percent": 45.0,
                    "width_percent": 30.0,
                    "height_percent": 3.0,
                },
                "description": (
                    "Texto 'SkyBrand' encontrado no corpo da página "
                    "em um parágrafo de descrição de produto."
                ),
            },
            {
                "match_type": "text",
                "confidence": 45,
                "bounding_box": {
                    "x_percent": 60.0,
                    "y_percent": 90.0,
                    "width_percent": 15.0,
                    "height_percent": 2.0,
                },
                "description": (
                    "Possível menção parcial de 'DGO' no footer, "
                    "mas contexto sugere sigla genérica."
                ),
            },
            {
                "match_type": "logo",
                "confidence": 60,
                "bounding_box": {
                    "x_percent": 80.0,
                    "y_percent": 95.0,
                    "width_percent": 5.0,
                    "height_percent": 3.0,
                },
                "description": (
                    "Ícone similar ao 'brand_icon.svg' no rodapé, "
                    "bastante pequeno e em escala de cinza."
                ),
            },
        ]
    }


@pytest.fixture
def analyzer_config() -> AnalyzerConfig:
    """Configuração realista para testes de integração."""
    return AnalyzerConfig(
        bedrock_model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        bedrock_region="us-east-1",
        confidence_threshold=70,
        request_timeout_seconds=60,
        max_retries=3,
        retry_base_delay_seconds=2.0,
    )


@pytest.fixture
def mock_bedrock_client() -> AsyncMock:
    """Mock do BedrockClient para integração."""
    return AsyncMock()


@pytest.fixture
def analyzer(
    analyzer_config: AnalyzerConfig,
    mock_bedrock_client: AsyncMock,
) -> Analyzer:
    """Analyzer com BedrockClient mockado."""
    return Analyzer(
        config=analyzer_config,
        bedrock_client=mock_bedrock_client,
    )


@pytest.mark.integration
class TestAnalysisFlowCompleto:
    """Testa o fluxo completo: screenshot → análise → DetectionResults.

    Valida a integração entre:
    - Leitura de arquivo real (file I/O)
    - Construção de prompt com brand assets reais
    - Parsing de resposta realista do Bedrock
    - Filtragem por confidence threshold
    """

    async def test_fluxo_completo_com_deteccoes_mistas(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
        realistic_bedrock_response: dict,
    ) -> None:
        """Pipeline completo deve produzir DetectionResults filtrados.

        Dado:
        - Screenshot PNG real no disco (4x4 pixels)
        - 4 brand assets (2 logos + 2 textos)
        - Resposta Bedrock com 4 detecções (conf: 92, 78, 45, 60)

        Esperado:
        - 3 detecções retornadas (confidence >= 60)
        - Detecção com confidence 45 filtrada
        """
        mock_bedrock_client.invoke_model.return_value = (
            realistic_bedrock_response
        )

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://site-suspeito.com.br/produtos",
            screenshot_ref_id="ref-integ-001",
        )

        # Deve retornar 3 detecções (92, 78, 60 passam; 45 filtrada)
        assert len(results) == 3
        confidences = [r.confidence for r in results]
        assert 92 in confidences
        assert 78 in confidences
        assert 60 in confidences
        assert 45 not in confidences

    async def test_field_mapping_correto(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
        realistic_bedrock_response: dict,
    ) -> None:
        """Campos dos DetectionResults devem estar mapeados corretamente.

        Valida: target_url, match_type, confidence, bounding_box.
        (Requirement 4.4)
        """
        mock_bedrock_client.invoke_model.return_value = (
            realistic_bedrock_response
        )

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://site-suspeito.com.br/produtos",
            screenshot_ref_id="ref-integ-001",
        )

        # Verifica target_url propagado para todos
        for detection in results:
            assert detection.target_url == (
                "https://site-suspeito.com.br/produtos"
            )
            assert detection.screenshot_ref_id == "ref-integ-001"

        # Verifica detecção de logo (confidence 92)
        logo_detection = next(
            r for r in results
            if r.confidence == 92
        )
        assert logo_detection.match_type == "logo"
        assert logo_detection.bounding_box.x_percent == 2.5
        assert logo_detection.bounding_box.y_percent == 1.0
        assert logo_detection.bounding_box.width_percent == 12.0
        assert logo_detection.bounding_box.height_percent == 8.0
        assert "brand_main.png" in logo_detection.description

        # Verifica detecção de texto (confidence 78)
        text_detection = next(
            r for r in results
            if r.confidence == 78
        )
        assert text_detection.match_type == "text"
        assert text_detection.bounding_box.x_percent == 25.0
        assert text_detection.bounding_box.y_percent == 45.0
        assert "SkyBrand" in text_detection.description

    async def test_confidence_threshold_boundary(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """Filtragem deve usar threshold CONFIRMED_MATCH_THRESHOLD (60).

        - Confidence 59: filtrada
        - Confidence 60: incluída (limite exato, Req 4.5)
        - Confidence 61: incluída
        """
        mock_bedrock_client.invoke_model.return_value = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 59,
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 10.0,
                        "width_percent": 10.0,
                        "height_percent": 10.0,
                    },
                    "description": "Logo abaixo do threshold",
                },
                {
                    "match_type": "text",
                    "confidence": 60,
                    "bounding_box": {
                        "x_percent": 20.0,
                        "y_percent": 20.0,
                        "width_percent": 15.0,
                        "height_percent": 5.0,
                    },
                    "description": "Texto no limite exato",
                },
                {
                    "match_type": "logo",
                    "confidence": 61,
                    "bounding_box": {
                        "x_percent": 50.0,
                        "y_percent": 50.0,
                        "width_percent": 8.0,
                        "height_percent": 6.0,
                    },
                    "description": "Logo acima do threshold",
                },
            ]
        }

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://example.com",
            screenshot_ref_id="ref-boundary",
        )

        assert len(results) == 2
        confidences = sorted(r.confidence for r in results)
        assert confidences == [60, 61]

    async def test_prompt_contem_todos_brand_assets(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """O prompt enviado ao Bedrock deve conter todos os assets.

        Verifica que a construção do prompt integra corretamente
        os brand assets reais (logos por filename, textos por valor).
        (Requirement 4.1)
        """
        mock_bedrock_client.invoke_model.return_value = {
            "detections": []
        }

        await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://example.com",
            screenshot_ref_id="ref-prompt",
        )

        # Verifica que invoke_model foi chamado com prompt adequado
        mock_bedrock_client.invoke_model.assert_called_once()
        call_args = mock_bedrock_client.invoke_model.call_args
        prompt_text = call_args[1]["prompt"] if "prompt" in (call_args[1] or {}) else call_args[0][1]

        # Prompt deve conter os filenames dos logos
        assert "brand_main.png" in prompt_text
        assert "brand_icon.svg" in prompt_text

        # Prompt deve conter os textos de marca
        assert "SkyBrand" in prompt_text
        assert "DGO Digital" in prompt_text

    async def test_leitura_real_de_arquivo_png(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """Deve ler arquivo PNG real do disco e enviar bytes ao Bedrock.

        Verifica integração do file I/O real com o pipeline.
        """
        mock_bedrock_client.invoke_model.return_value = {
            "detections": []
        }

        await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://example.com",
            screenshot_ref_id="ref-io",
        )

        # Verifica que bytes reais do PNG foram passados
        call_args = mock_bedrock_client.invoke_model.call_args
        image_bytes = call_args[1]["image_bytes"] if "image_bytes" in (call_args[1] or {}) else call_args[0][0]

        # Deve ser um PNG válido (magic bytes)
        assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        # Deve ter o conteúdo do arquivo no disco
        assert image_bytes == real_screenshot.read_bytes()

    async def test_nenhuma_deteccao_retorna_lista_vazia(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """Quando Bedrock não detecta nada, deve retornar lista vazia."""
        mock_bedrock_client.invoke_model.return_value = {
            "detections": []
        }

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://site-limpo.com",
            screenshot_ref_id="ref-clean",
        )

        assert results == []

    async def test_todas_deteccoes_abaixo_threshold(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """Quando todas as detecções estão abaixo do threshold, retorna vazio."""
        mock_bedrock_client.invoke_model.return_value = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 30,
                    "bounding_box": {
                        "x_percent": 5.0,
                        "y_percent": 5.0,
                        "width_percent": 10.0,
                        "height_percent": 10.0,
                    },
                    "description": "Logo muito incerto",
                },
                {
                    "match_type": "text",
                    "confidence": 55,
                    "bounding_box": {
                        "x_percent": 40.0,
                        "y_percent": 60.0,
                        "width_percent": 20.0,
                        "height_percent": 4.0,
                    },
                    "description": "Texto abaixo do threshold",
                },
            ]
        }

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://site-falso-positivo.com",
            screenshot_ref_id="ref-below",
        )

        assert results == []

    async def test_deteccao_com_bounding_box_extremos(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """Bounding boxes com valores extremos (0% e 100%) são aceitos."""
        mock_bedrock_client.invoke_model.return_value = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 95,
                    "bounding_box": {
                        "x_percent": 0.0,
                        "y_percent": 0.0,
                        "width_percent": 100.0,
                        "height_percent": 100.0,
                    },
                    "description": "Logo ocupa a página inteira",
                },
            ]
        }

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://exemplo.com",
            screenshot_ref_id="ref-extremo",
        )

        assert len(results) == 1
        bbox = results[0].bounding_box
        assert bbox.x_percent == 0.0
        assert bbox.y_percent == 0.0
        assert bbox.width_percent == 100.0
        assert bbox.height_percent == 100.0


@pytest.mark.integration
class TestAnalysisFlowErrorHandling:
    """Testa cenários de erro no fluxo integrado."""

    async def test_screenshot_inexistente_levanta_erro(
        self,
        analyzer: Analyzer,
        brand_assets_logo_e_texto: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Arquivo PNG inexistente deve levantar AnalysisIncompleteError."""
        fake_path = tmp_path / "nao_existe.png"

        with pytest.raises(AnalysisIncompleteError) as exc_info:
            await analyzer.analyze(
                screenshot_path=fake_path,
                brand_assets=brand_assets_logo_e_texto,
                target_url="https://example.com",
                screenshot_ref_id="ref-err",
            )

        assert "ler o screenshot" in str(exc_info.value)

    async def test_bedrock_falha_propaga_erro(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """Falha do Bedrock após retries deve levantar AnalysisIncompleteError."""
        mock_bedrock_client.invoke_model.side_effect = RuntimeError(
            "Service unavailable"
        )

        with pytest.raises(AnalysisIncompleteError) as exc_info:
            await analyzer.analyze(
                screenshot_path=real_screenshot,
                brand_assets=brand_assets_logo_e_texto,
                target_url="https://site-alvo.com",
                screenshot_ref_id="ref-fail",
            )

        assert "https://site-alvo.com" in str(exc_info.value)

    async def test_resposta_com_deteccoes_parcialmente_invalidas(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
        brand_assets_logo_e_texto: list[BrandAsset],
    ) -> None:
        """Detecções inválidas são ignoradas, válidas são retornadas.

        Simula resposta onde parte dos itens está malformada,
        validando que o pipeline é resiliente.
        """
        mock_bedrock_client.invoke_model.return_value = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 88,
                    "bounding_box": {
                        "x_percent": 5.0,
                        "y_percent": 5.0,
                        "width_percent": 10.0,
                        "height_percent": 8.0,
                    },
                    "description": "Detecção válida",
                },
                {
                    # match_type inválido
                    "match_type": "video",
                    "confidence": 90,
                    "bounding_box": {
                        "x_percent": 50.0,
                        "y_percent": 50.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Tipo inválido",
                },
                {
                    # Sem bounding_box
                    "match_type": "text",
                    "confidence": 75,
                    "description": "Sem localização",
                },
                {
                    "match_type": "text",
                    "confidence": 65,
                    "bounding_box": {
                        "x_percent": 30.0,
                        "y_percent": 70.0,
                        "width_percent": 25.0,
                        "height_percent": 4.0,
                    },
                    "description": "Segunda detecção válida",
                },
            ]
        }

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=brand_assets_logo_e_texto,
            target_url="https://site-parcial.com",
            screenshot_ref_id="ref-partial",
        )

        # Apenas 2 detecções válidas com confidence >= 60
        assert len(results) == 2
        confidences = sorted(r.confidence for r in results)
        assert confidences == [65, 88]

    async def test_assets_somente_logos(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
    ) -> None:
        """Pipeline funciona com apenas logos (sem textos)."""
        logo_assets = [
            BrandAsset(
                id="logo-only",
                asset_type="logo",
                file_path=Path("/assets/logo.png"),
                text_value=None,
                content_hash="hash_logo",
                original_filename="my_brand.png",
                file_size_bytes=3000,
                created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
            ),
        ]
        mock_bedrock_client.invoke_model.return_value = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 80,
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 10.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Logo detectado",
                },
            ]
        }

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=logo_assets,
            target_url="https://logo-only.com",
            screenshot_ref_id="ref-logo",
        )

        assert len(results) == 1
        assert results[0].match_type == "logo"

    async def test_assets_somente_textos(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        real_screenshot: Path,
    ) -> None:
        """Pipeline funciona com apenas textos (sem logos)."""
        text_assets = [
            BrandAsset(
                id="text-only",
                asset_type="text",
                file_path=None,
                text_value="MinhaEmpresa",
                content_hash="hash_text",
                original_filename=None,
                file_size_bytes=None,
                created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
            ),
        ]
        mock_bedrock_client.invoke_model.return_value = {
            "detections": [
                {
                    "match_type": "text",
                    "confidence": 70,
                    "bounding_box": {
                        "x_percent": 20.0,
                        "y_percent": 40.0,
                        "width_percent": 30.0,
                        "height_percent": 5.0,
                    },
                    "description": "Texto 'MinhaEmpresa' encontrado",
                },
            ]
        }

        results = await analyzer.analyze(
            screenshot_path=real_screenshot,
            brand_assets=text_assets,
            target_url="https://text-only.com",
            screenshot_ref_id="ref-text",
        )

        assert len(results) == 1
        assert results[0].match_type == "text"
        assert results[0].confidence == 70
