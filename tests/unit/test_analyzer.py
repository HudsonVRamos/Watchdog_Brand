"""Testes unitários para o Analyzer."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from botocore.exceptions import BotoCoreError, ClientError

from brand_watchdog.analyzer.analyzer import (
    Analyzer,
    AnalysisIncompleteError,
    CONFIRMED_MATCH_THRESHOLD,
)
from brand_watchdog.config import AnalyzerConfig
from brand_watchdog.models.dataclasses import BrandAsset


@pytest.fixture
def analyzer_config() -> AnalyzerConfig:
    """Configuração padrão para testes."""
    return AnalyzerConfig(
        confidence_threshold=70,
        request_timeout_seconds=60,
        max_retries=3,
    )


@pytest.fixture
def mock_bedrock_client() -> AsyncMock:
    """Mock do BedrockClient."""
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


@pytest.fixture
def sample_brand_assets() -> list[BrandAsset]:
    """Ativos de marca de exemplo."""
    return [
        BrandAsset(
            id="asset-1",
            asset_type="logo",
            file_path=Path("/assets/logo.png"),
            text_value=None,
            content_hash="abc123",
            original_filename="brand_logo.png",
            file_size_bytes=1024,
            created_at=datetime.now(timezone.utc),
        ),
        BrandAsset(
            id="asset-2",
            asset_type="text",
            file_path=None,
            text_value="MinhaMarca",
            content_hash="def456",
            original_filename=None,
            file_size_bytes=None,
            created_at=datetime.now(timezone.utc),
        ),
    ]


@pytest.fixture
def sample_bedrock_response() -> dict:
    """Resposta de exemplo do Bedrock com detecções."""
    return {
        "detections": [
            {
                "match_type": "logo",
                "confidence": 85,
                "bounding_box": {
                    "x_percent": 10.0,
                    "y_percent": 5.0,
                    "width_percent": 20.0,
                    "height_percent": 15.0,
                },
                "description": "Logo da marca encontrado no header",
            },
            {
                "match_type": "text",
                "confidence": 72,
                "bounding_box": {
                    "x_percent": 30.0,
                    "y_percent": 50.0,
                    "width_percent": 40.0,
                    "height_percent": 5.0,
                },
                "description": "Texto 'MinhaMarca' no corpo da página",
            },
        ]
    }


class TestAnalyzerAnalyze:
    """Testes do método analyze."""

    async def test_analyze_retorna_deteccoes_confirmadas(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        sample_bedrock_response: dict,
        tmp_path: Path,
    ) -> None:
        """Deve retornar detecções com confidence >= threshold."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.return_value = (
            sample_bedrock_response
        )

        results = await analyzer.analyze(
            screenshot_path=screenshot,
            brand_assets=sample_brand_assets,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert len(results) == 2
        assert all(
            r.confidence >= CONFIRMED_MATCH_THRESHOLD
            for r in results
        )

    async def test_analyze_filtra_baixa_confidence(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Deve filtrar detecções abaixo do threshold de 60."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.return_value = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 59,
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Logo parcial",
                },
                {
                    "match_type": "text",
                    "confidence": 60,
                    "bounding_box": {
                        "x_percent": 30.0,
                        "y_percent": 50.0,
                        "width_percent": 40.0,
                        "height_percent": 5.0,
                    },
                    "description": "Texto com confidence exata",
                },
            ]
        }

        results = await analyzer.analyze(
            screenshot_path=screenshot,
            brand_assets=sample_brand_assets,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        # Apenas a detecção com confidence 60 deve passar
        assert len(results) == 1
        assert results[0].confidence == 60

    async def test_analyze_sem_brand_assets_retorna_vazio(
        self,
        analyzer: Analyzer,
        tmp_path: Path,
    ) -> None:
        """Deve retornar lista vazia se não houver ativos de marca."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")

        results = await analyzer.analyze(
            screenshot_path=screenshot,
            brand_assets=[],
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    async def test_analyze_screenshot_inexistente_raise_error(
        self,
        analyzer: Analyzer,
        sample_brand_assets: list[BrandAsset],
    ) -> None:
        """Deve levantar AnalysisIncompleteError se screenshot não existir."""
        with pytest.raises(AnalysisIncompleteError):
            await analyzer.analyze(
                screenshot_path=Path("/nao/existe.png"),
                brand_assets=sample_brand_assets,
                target_url="https://example.com",
                screenshot_ref_id="ref-001",
            )

    async def test_analyze_bedrock_falha_raise_incomplete(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Deve levantar AnalysisIncompleteError quando Bedrock falha."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.side_effect = RuntimeError(
            "Bedrock indisponível"
        )

        with pytest.raises(AnalysisIncompleteError):
            await analyzer.analyze(
                screenshot_path=screenshot,
                brand_assets=sample_brand_assets,
                target_url="https://example.com",
                screenshot_ref_id="ref-001",
            )

    async def test_analyze_resposta_vazia_retorna_vazio(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Deve retornar lista vazia quando não há detecções."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.return_value = {
            "detections": []
        }

        results = await analyzer.analyze(
            screenshot_path=screenshot,
            brand_assets=sample_brand_assets,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []


class TestBuildAnalysisPrompt:
    """Testes do _build_analysis_prompt."""

    def test_prompt_inclui_logos_e_textos(
        self,
        analyzer: Analyzer,
        sample_brand_assets: list[BrandAsset],
    ) -> None:
        """Deve incluir nomes de logos e textos no prompt."""
        prompt = analyzer._build_analysis_prompt(sample_brand_assets)

        assert "brand_logo.png" in prompt
        assert "MinhaMarca" in prompt
        assert "LOGOS para detectar" in prompt
        assert "TEXTOS para detectar" in prompt

    def test_prompt_somente_logos(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve funcionar com apenas logos."""
        assets = [
            BrandAsset(
                id="a1",
                asset_type="logo",
                file_path=Path("/logo.png"),
                text_value=None,
                content_hash="h1",
                original_filename="meu_logo.png",
                file_size_bytes=512,
                created_at=datetime.now(timezone.utc),
            )
        ]

        prompt = analyzer._build_analysis_prompt(assets)

        assert "meu_logo.png" in prompt
        assert "TEXTOS para detectar: (nenhum)" in prompt

    def test_prompt_somente_textos(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve funcionar com apenas textos."""
        assets = [
            BrandAsset(
                id="a1",
                asset_type="text",
                file_path=None,
                text_value="BrandName",
                content_hash="h1",
                original_filename=None,
                file_size_bytes=None,
                created_at=datetime.now(timezone.utc),
            )
        ]

        prompt = analyzer._build_analysis_prompt(assets)

        assert "BrandName" in prompt
        assert "LOGOS para detectar: (nenhum)" in prompt

    def test_prompt_exige_json_retorno(
        self,
        analyzer: Analyzer,
        sample_brand_assets: list[BrandAsset],
    ) -> None:
        """Deve instruir retorno em formato JSON."""
        prompt = analyzer._build_analysis_prompt(sample_brand_assets)

        assert "JSON" in prompt
        assert "detections" in prompt
        assert "match_type" in prompt
        assert "bounding_box" in prompt


class TestParseDetectionResponse:
    """Testes do _parse_detection_response."""

    def test_parse_resposta_valida(
        self,
        analyzer: Analyzer,
        sample_bedrock_response: dict,
    ) -> None:
        """Deve parsear resposta válida corretamente."""
        results = analyzer._parse_detection_response(
            response=sample_bedrock_response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert len(results) == 2
        assert results[0].match_type == "logo"
        assert results[0].confidence == 85
        assert results[0].bounding_box.x_percent == 10.0
        assert results[1].match_type == "text"
        assert results[1].confidence == 72

    def test_parse_resposta_sem_detections_key(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve retornar lista vazia se key 'detections' ausente."""
        results = analyzer._parse_detection_response(
            response={"other_key": "value"},
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_ignora_deteccoes_invalidas(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar itens com dados inválidos e continuar."""
        response = {
            "detections": [
                {
                    "match_type": "invalid_type",
                    "confidence": 80,
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Inválido",
                },
                {
                    "match_type": "logo",
                    "confidence": 90,
                    "bounding_box": {
                        "x_percent": 5.0,
                        "y_percent": 10.0,
                        "width_percent": 15.0,
                        "height_percent": 10.0,
                    },
                    "description": "Válido",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        # Apenas o item válido deve estar presente
        assert len(results) == 1
        assert results[0].match_type == "logo"
        assert results[0].confidence == 90

    def test_parse_confidence_fora_do_intervalo(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve rejeitar confidence fora de 0-100."""
        response = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 150,
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Confiança inválida",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_detections_nao_lista(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve retornar vazio se detections não for lista."""
        results = analyzer._parse_detection_response(
            response={"detections": "not a list"},
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_preserva_target_url_e_ref_id(
        self,
        analyzer: Analyzer,
        sample_bedrock_response: dict,
    ) -> None:
        """Deve propagar target_url e screenshot_ref_id."""
        results = analyzer._parse_detection_response(
            response=sample_bedrock_response,
            target_url="https://test.com",
            screenshot_ref_id="ref-abc",
        )

        for r in results:
            assert r.target_url == "https://test.com"
            assert r.screenshot_ref_id == "ref-abc"


class TestRetryExhaustion:
    """Testes para cenários de exaustão de retries (Req 4.6, 4.7)."""

    async def test_botocore_error_apos_retries_raise_incomplete(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Deve levantar AnalysisIncompleteError com BotoCoreError."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.side_effect = BotoCoreError()

        with pytest.raises(AnalysisIncompleteError) as exc_info:
            await analyzer.analyze(
                screenshot_path=screenshot,
                brand_assets=sample_brand_assets,
                target_url="https://example.com",
                screenshot_ref_id="ref-001",
            )

        assert "falha após tentativas de retry" in str(exc_info.value)

    async def test_client_error_apos_retries_raise_incomplete(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Deve levantar AnalysisIncompleteError com ClientError."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.side_effect = ClientError(
            error_response={"Error": {"Code": "ThrottlingException",
                                      "Message": "Rate exceeded"}},
            operation_name="InvokeModel",
        )

        with pytest.raises(AnalysisIncompleteError):
            await analyzer.analyze(
                screenshot_path=screenshot,
                brand_assets=sample_brand_assets,
                target_url="https://example.com",
                screenshot_ref_id="ref-001",
            )

    async def test_timeout_error_apos_retries_raise_incomplete(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Deve levantar AnalysisIncompleteError com TimeoutError (Req 4.8)."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.side_effect = TimeoutError(
            "Timeout de 60 segundos excedido"
        )

        with pytest.raises(AnalysisIncompleteError) as exc_info:
            await analyzer.analyze(
                screenshot_path=screenshot,
                brand_assets=sample_brand_assets,
                target_url="https://example.com",
                screenshot_ref_id="ref-001",
            )

        assert "falha após tentativas de retry" in str(exc_info.value)

    async def test_mensagem_erro_inclui_target_url(
        self,
        analyzer: Analyzer,
        mock_bedrock_client: AsyncMock,
        sample_brand_assets: list[BrandAsset],
        tmp_path: Path,
    ) -> None:
        """Mensagem de erro deve incluir a URL do site para rastreio."""
        screenshot = tmp_path / "screenshot.png"
        screenshot.write_bytes(b"fake-png-data")
        mock_bedrock_client.invoke_model.side_effect = TimeoutError(
            "timeout"
        )

        with pytest.raises(AnalysisIncompleteError) as exc_info:
            await analyzer.analyze(
                screenshot_path=screenshot,
                brand_assets=sample_brand_assets,
                target_url="https://alvo.com.br",
                screenshot_ref_id="ref-002",
            )

        assert "https://alvo.com.br" in str(exc_info.value)


class TestParsingMalformedResponse:
    """Testes para parsing de respostas malformadas do Bedrock (Req 4.4)."""

    def test_parse_deteccao_sem_bounding_box(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar detecção sem chave bounding_box."""
        response = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 80,
                    "description": "Logo sem bbox",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_deteccao_sem_match_type(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar detecção sem chave match_type."""
        response = {
            "detections": [
                {
                    "confidence": 90,
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Sem match_type",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_deteccao_sem_confidence(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar detecção sem chave confidence."""
        response = {
            "detections": [
                {
                    "match_type": "text",
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Sem confidence",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_confidence_tipo_errado_string(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve converter confidence string numérica para int."""
        response = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": "85",
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Confidence como string",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        # int("85") funciona, então deve parsear com sucesso
        assert len(results) == 1
        assert results[0].confidence == 85

    def test_parse_confidence_string_invalida(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar detecção com confidence não conversível."""
        response = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": "muito alta",
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Confidence inválida",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_bounding_box_campos_incompletos(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar detecção com bounding_box incompleto."""
        response = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": 80,
                    "bounding_box": {
                        "x_percent": 10.0,
                        # Faltam y_percent, width_percent, height_percent
                    },
                    "description": "Bbox incompleto",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_bounding_box_como_string(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar detecção com bounding_box como tipo errado."""
        response = {
            "detections": [
                {
                    "match_type": "text",
                    "confidence": 75,
                    "bounding_box": "top-left",
                    "description": "Bbox inválido",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_resposta_completamente_vazia(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve retornar vazio para resposta sem nenhuma chave."""
        results = analyzer._parse_detection_response(
            response={},
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []

    def test_parse_deteccao_item_none(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar item None na lista de detecções."""
        response = {
            "detections": [
                None,
                {
                    "match_type": "logo",
                    "confidence": 90,
                    "bounding_box": {
                        "x_percent": 5.0,
                        "y_percent": 10.0,
                        "width_percent": 15.0,
                        "height_percent": 10.0,
                    },
                    "description": "Válido após None",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        # Item None deve ser ignorado, item válido processado
        assert len(results) == 1
        assert results[0].confidence == 90

    def test_parse_confidence_negativa(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve rejeitar confidence negativa."""
        response = {
            "detections": [
                {
                    "match_type": "logo",
                    "confidence": -10,
                    "bounding_box": {
                        "x_percent": 10.0,
                        "y_percent": 5.0,
                        "width_percent": 20.0,
                        "height_percent": 15.0,
                    },
                    "description": "Confidence negativa",
                },
            ]
        }

        results = analyzer._parse_detection_response(
            response=response,
            target_url="https://example.com",
            screenshot_ref_id="ref-001",
        )

        assert results == []


class TestPromptBuildingEdgeCases:
    """Testes de _build_analysis_prompt com edge cases (Req 4.1)."""

    def test_prompt_logo_sem_filename(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve usar 'logo sem nome' quando original_filename é None."""
        assets = [
            BrandAsset(
                id="a1",
                asset_type="logo",
                file_path=Path("/assets/logo.png"),
                text_value=None,
                content_hash="h1",
                original_filename=None,
                file_size_bytes=2048,
                created_at=datetime.now(timezone.utc),
            )
        ]

        prompt = analyzer._build_analysis_prompt(assets)

        assert "logo sem nome" in prompt

    def test_prompt_texto_muito_longo(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve incluir texto longo sem truncar no prompt."""
        long_text = "MarcaExemplo" * 50  # 600 chars
        assets = [
            BrandAsset(
                id="a1",
                asset_type="text",
                file_path=None,
                text_value=long_text,
                content_hash="h1",
                original_filename=None,
                file_size_bytes=None,
                created_at=datetime.now(timezone.utc),
            )
        ]

        prompt = analyzer._build_analysis_prompt(assets)

        assert long_text in prompt

    def test_prompt_multiplos_assets_misturados(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve listar todos os logos e textos separados corretamente."""
        assets = [
            BrandAsset(
                id="a1",
                asset_type="logo",
                file_path=Path("/logo1.png"),
                text_value=None,
                content_hash="h1",
                original_filename="logo_azul.png",
                file_size_bytes=512,
                created_at=datetime.now(timezone.utc),
            ),
            BrandAsset(
                id="a2",
                asset_type="logo",
                file_path=Path("/logo2.png"),
                text_value=None,
                content_hash="h2",
                original_filename="logo_verde.png",
                file_size_bytes=1024,
                created_at=datetime.now(timezone.utc),
            ),
            BrandAsset(
                id="a3",
                asset_type="text",
                file_path=None,
                text_value="BrandA",
                content_hash="h3",
                original_filename=None,
                file_size_bytes=None,
                created_at=datetime.now(timezone.utc),
            ),
            BrandAsset(
                id="a4",
                asset_type="text",
                file_path=None,
                text_value="BrandB",
                content_hash="h4",
                original_filename=None,
                file_size_bytes=None,
                created_at=datetime.now(timezone.utc),
            ),
        ]

        prompt = analyzer._build_analysis_prompt(assets)

        assert "logo_azul.png" in prompt
        assert "logo_verde.png" in prompt
        assert "BrandA" in prompt
        assert "BrandB" in prompt

    def test_prompt_texto_com_caracteres_especiais(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve incluir textos com caracteres especiais no prompt."""
        assets = [
            BrandAsset(
                id="a1",
                asset_type="text",
                file_path=None,
                text_value='Marca "Especial" & Cia™',
                content_hash="h1",
                original_filename=None,
                file_size_bytes=None,
                created_at=datetime.now(timezone.utc),
            )
        ]

        prompt = analyzer._build_analysis_prompt(assets)

        assert 'Marca "Especial" & Cia™' in prompt

    def test_prompt_asset_texto_com_text_value_none(
        self,
        analyzer: Analyzer,
    ) -> None:
        """Deve ignorar asset do tipo texto com text_value None."""
        assets = [
            BrandAsset(
                id="a1",
                asset_type="text",
                file_path=None,
                text_value=None,
                content_hash="h1",
                original_filename=None,
                file_size_bytes=None,
                created_at=datetime.now(timezone.utc),
            )
        ]

        prompt = analyzer._build_analysis_prompt(assets)

        # Sem textos válidos, deve mostrar "(nenhum)"
        assert "TEXTOS para detectar: (nenhum)" in prompt
