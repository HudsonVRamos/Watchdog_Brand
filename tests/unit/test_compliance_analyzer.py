"""Testes unitários para ComplianceAnalyzer.

Valida o fluxo completo de análise de compliance, parsing de respostas,
persistência de violações e tratamento de erros.

Requisitos cobertos: 7.1, 7.6, 9.3, 9.4
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.analyzer.compliance_analyzer import ComplianceAnalyzer
from brand_watchdog.analyzer.compliance_exceptions import (
    ComplianceParseError,
    CompliancePersistenceError,
)
from brand_watchdog.analyzer.compliance_prompt_builder import PromptPayload
from brand_watchdog.config import AnalyzerConfig, StorageConfig


# --- Fixtures ---


@pytest.fixture
def analyzer_config() -> AnalyzerConfig:
    """Configuração padrão do analisador para testes."""
    return AnalyzerConfig()


@pytest.fixture
def storage_config() -> StorageConfig:
    """Configuração padrão de storage para testes."""
    return StorageConfig(detection_retention_days=90)


@pytest.fixture
def mock_prompt_builder() -> MagicMock:
    """Mock do PromptBuilder que retorna payload válido."""
    builder = MagicMock()
    builder.build_prompt.return_value = PromptPayload(
        images=[(b"fake_screenshot_bytes", "screenshot_under_analysis")],
        prompt_text="Regras de compliance...",
    )
    return builder


@pytest.fixture
def valid_bedrock_response() -> dict:
    """Resposta válida do Bedrock com todas as 6 regras."""
    return {
        "compliance_results": [
            {
                "rule_id": "facilitator_role",
                "status": "PASS",
                "confidence": 92,
                "description": "SKY+ referenciado como facilitador.",
            },
            {
                "rule_id": "logo_application",
                "status": "PASS",
                "confidence": 88,
                "description": "Logos na ordem correta.",
            },
            {
                "rule_id": "logo_effects",
                "status": "FAIL",
                "confidence": 75,
                "description": "Sombra detectada no logo.",
            },
            {
                "rule_id": "content_separation",
                "status": "PASS",
                "confidence": 95,
                "description": "Conteúdo separado por blocos.",
            },
            {
                "rule_id": "naming_pricing",
                "status": "FAIL",
                "confidence": 80,
                "description": "Preço abaixo de R$80.",
            },
            {
                "rule_id": "kv_integrity",
                "status": "PASS",
                "confidence": 90,
                "description": "KV sem alterações.",
            },
        ]
    }


@pytest.fixture
def mock_bedrock_client(valid_bedrock_response: dict) -> AsyncMock:
    """Mock do BedrockClient com resposta válida."""
    client = AsyncMock()
    client.invoke_model_multi = AsyncMock(
        return_value=valid_bedrock_response
    )
    return client


@pytest.fixture
def mock_detection_store() -> AsyncMock:
    """Mock do DetectionStore com save bem-sucedido."""
    store = AsyncMock()
    store.save = AsyncMock(return_value="detection-id-123")
    return store


@pytest.fixture
def analyzer(
    analyzer_config: AnalyzerConfig,
    mock_bedrock_client: AsyncMock,
    mock_prompt_builder: MagicMock,
    mock_detection_store: AsyncMock,
    storage_config: StorageConfig,
) -> ComplianceAnalyzer:
    """ComplianceAnalyzer configurado com mocks."""
    return ComplianceAnalyzer(
        config=analyzer_config,
        bedrock_client=mock_bedrock_client,
        prompt_builder=mock_prompt_builder,
        detection_store=mock_detection_store,
        storage_config=storage_config,
    )


# --- Testes de fluxo completo com resposta válida ---


class TestFluxoCompletoRespostaValida:
    """Testa o fluxo completo com BedrockClient retornando resposta válida."""

    @pytest.mark.asyncio
    async def test_retorna_compliance_report_valido(
        self, analyzer: ComplianceAnalyzer
    ) -> None:
        """Fluxo completo retorna ComplianceReport com todos os campos."""
        report = await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp-example.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        assert report.target_url == "https://isp-example.com"
        assert report.screenshot_ref_id == "ref-001"
        assert report.cycle_id == "cycle-001"
        assert report.overall_status == "non_compliant"
        assert len(report.rule_results) == 6

    @pytest.mark.asyncio
    async def test_invoca_prompt_builder_com_screenshot(
        self,
        analyzer: ComplianceAnalyzer,
        mock_prompt_builder: MagicMock,
    ) -> None:
        """PromptBuilder.build_prompt é chamado com screenshot_path."""
        screenshot = Path("/tmp/screenshot.png")
        await analyzer.analyze_compliance(
            screenshot_path=screenshot,
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        mock_prompt_builder.build_prompt.assert_called_once_with(screenshot)

    @pytest.mark.asyncio
    async def test_invoca_bedrock_com_images_e_prompt(
        self,
        analyzer: ComplianceAnalyzer,
        mock_bedrock_client: AsyncMock,
    ) -> None:
        """BedrockClient.invoke_model_multi é chamado com payload correto."""
        await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        mock_bedrock_client.invoke_model_multi.assert_called_once_with(
            [(b"fake_screenshot_bytes", "screenshot_under_analysis")],
            "Regras de compliance...",
        )

    @pytest.mark.asyncio
    async def test_report_contem_regras_pass_e_fail(
        self, analyzer: ComplianceAnalyzer
    ) -> None:
        """Report contém regras com diferentes statuses."""
        report = await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        statuses = {r.rule_id: r.status for r in report.rule_results}
        assert statuses["facilitator_role"] == "PASS"
        assert statuses["logo_effects"] == "FAIL"
        assert statuses["naming_pricing"] == "FAIL"


# --- Testes de resposta inválida do Bedrock (ComplianceParseError) ---


class TestRespostaInvalidaBedrock:
    """Testa handling de resposta inválida do Bedrock."""

    @pytest.mark.asyncio
    async def test_resposta_sem_compliance_results_levanta_parse_error(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        mock_detection_store: AsyncMock,
        storage_config: StorageConfig,
    ) -> None:
        """Resposta sem chave 'compliance_results' levanta ComplianceParseError."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={"invalid_key": []}
        )

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=mock_detection_store,
            storage_config=storage_config,
        )

        with pytest.raises(ComplianceParseError):
            await analyzer.analyze_compliance(
                screenshot_path=Path("/tmp/screenshot.png"),
                target_url="https://isp.com",
                screenshot_ref_id="ref-001",
                cycle_id="cycle-001",
            )

    @pytest.mark.asyncio
    async def test_resposta_com_regras_faltantes_levanta_parse_error(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        mock_detection_store: AsyncMock,
        storage_config: StorageConfig,
    ) -> None:
        """Resposta com regras faltantes levanta ComplianceParseError."""
        bedrock_client = AsyncMock()
        # Apenas 2 regras em vez de 6
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                    {
                        "rule_id": "logo_application",
                        "status": "PASS",
                        "confidence": 85,
                        "description": "OK",
                    },
                ]
            }
        )

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=mock_detection_store,
            storage_config=storage_config,
        )

        with pytest.raises(ComplianceParseError):
            await analyzer.analyze_compliance(
                screenshot_path=Path("/tmp/screenshot.png"),
                target_url="https://isp.com",
                screenshot_ref_id="ref-001",
                cycle_id="cycle-001",
            )

    @pytest.mark.asyncio
    async def test_resposta_com_status_invalido_levanta_parse_error(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        mock_detection_store: AsyncMock,
        storage_config: StorageConfig,
    ) -> None:
        """Resposta com status inválido levanta ComplianceParseError."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "INVALID_STATUS",
                        "confidence": 90,
                        "description": "Desc",
                    },
                ]
            }
        )

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=mock_detection_store,
            storage_config=storage_config,
        )

        with pytest.raises(ComplianceParseError):
            await analyzer.analyze_compliance(
                screenshot_path=Path("/tmp/screenshot.png"),
                target_url="https://isp.com",
                screenshot_ref_id="ref-001",
                cycle_id="cycle-001",
            )


# --- Testes de persistência com DetectionStore mockado ---


class TestPersistenciaViolacoes:
    """Testa persistência de violações (regras FAIL) com DetectionStore."""

    @pytest.mark.asyncio
    async def test_persiste_violacoes_via_detection_store(
        self,
        analyzer: ComplianceAnalyzer,
        mock_detection_store: AsyncMock,
    ) -> None:
        """Regras FAIL geram chamadas ao DetectionStore.save."""
        await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        # 2 regras FAIL: logo_effects e naming_pricing
        assert mock_detection_store.save.call_count == 2

    @pytest.mark.asyncio
    async def test_detection_result_usa_rule_id_como_match_type(
        self,
        analyzer: ComplianceAnalyzer,
        mock_detection_store: AsyncMock,
    ) -> None:
        """DetectionResult usa rule_id como match_type."""
        await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        # Verificar os match_types das chamadas
        call_args_list = mock_detection_store.save.call_args_list
        match_types = [
            call.kwargs["detection"].match_type
            for call in call_args_list
        ]
        assert "logo_effects" in match_types
        assert "naming_pricing" in match_types

    @pytest.mark.asyncio
    async def test_detection_result_usa_confidence_da_regra(
        self,
        analyzer: ComplianceAnalyzer,
        mock_detection_store: AsyncMock,
    ) -> None:
        """DetectionResult herda confidence da ComplianceRuleResult."""
        await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        call_args_list = mock_detection_store.save.call_args_list
        confidences = {
            call.kwargs["detection"].match_type: call.kwargs["detection"].confidence
            for call in call_args_list
        }
        assert confidences["logo_effects"] == 75
        assert confidences["naming_pricing"] == 80

    @pytest.mark.asyncio
    async def test_detection_result_com_bounding_box_zerada(
        self,
        analyzer: ComplianceAnalyzer,
        mock_detection_store: AsyncMock,
    ) -> None:
        """DetectionResult tem bounding box com coordenadas 0.0."""
        await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        for call in mock_detection_store.save.call_args_list:
            detection = call.kwargs["detection"]
            assert detection.bounding_box.x_percent == 0.0
            assert detection.bounding_box.y_percent == 0.0
            assert detection.bounding_box.width_percent == 0.0
            assert detection.bounding_box.height_percent == 0.0

    @pytest.mark.asyncio
    async def test_save_recebe_target_site_id_e_cycle_id(
        self,
        analyzer: ComplianceAnalyzer,
        mock_detection_store: AsyncMock,
    ) -> None:
        """DetectionStore.save recebe target_site_id e monitoring_cycle_id."""
        await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        for call in mock_detection_store.save.call_args_list:
            assert call.kwargs["target_site_id"] == "https://isp.com"
            assert call.kwargs["monitoring_cycle_id"] == "cycle-001"


# --- Testes: apenas regras FAIL geram DetectionResult ---


class TestApenasFailGeraDetection:
    """Testa que apenas regras com status FAIL geram DetectionResult."""

    @pytest.mark.asyncio
    async def test_regras_pass_nao_geram_detection(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        storage_config: StorageConfig,
    ) -> None:
        """Regras com status PASS não geram persistência."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "PASS",
                        "confidence": 92,
                        "description": "OK",
                    },
                    {
                        "rule_id": "logo_application",
                        "status": "PASS",
                        "confidence": 88,
                        "description": "OK",
                    },
                    {
                        "rule_id": "logo_effects",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                    {
                        "rule_id": "content_separation",
                        "status": "PASS",
                        "confidence": 95,
                        "description": "OK",
                    },
                    {
                        "rule_id": "naming_pricing",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                    {
                        "rule_id": "kv_integrity",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                ]
            }
        )

        detection_store = AsyncMock()
        detection_store.save = AsyncMock()

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=detection_store,
            storage_config=storage_config,
        )

        report = await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        assert report.overall_status == "compliant"
        detection_store.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_applicable_nao_gera_detection(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        storage_config: StorageConfig,
    ) -> None:
        """Regras NOT_APPLICABLE não geram persistência."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "NOT_APPLICABLE",
                        "confidence": 85,
                        "description": "Sem menção Amazon.",
                    },
                    {
                        "rule_id": "logo_application",
                        "status": "NOT_APPLICABLE",
                        "confidence": 80,
                        "description": "Sem logos.",
                    },
                    {
                        "rule_id": "logo_effects",
                        "status": "NOT_APPLICABLE",
                        "confidence": 80,
                        "description": "Sem logos.",
                    },
                    {
                        "rule_id": "content_separation",
                        "status": "NOT_APPLICABLE",
                        "confidence": 80,
                        "description": "Sem conteúdo parceiro.",
                    },
                    {
                        "rule_id": "naming_pricing",
                        "status": "NOT_APPLICABLE",
                        "confidence": 80,
                        "description": "Sem preço.",
                    },
                    {
                        "rule_id": "kv_integrity",
                        "status": "NOT_APPLICABLE",
                        "confidence": 80,
                        "description": "Sem KV.",
                    },
                ]
            }
        )

        detection_store = AsyncMock()
        detection_store.save = AsyncMock()

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=detection_store,
            storage_config=storage_config,
        )

        report = await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        assert report.overall_status == "compliant"
        detection_store.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_apenas_regras_fail_contadas_para_persistencia(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        storage_config: StorageConfig,
    ) -> None:
        """Apenas regras FAIL geram DetectionResult; PASS e NOT_APPLICABLE não."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "FAIL",
                        "confidence": 90,
                        "description": "Falha no facilitador.",
                    },
                    {
                        "rule_id": "logo_application",
                        "status": "PASS",
                        "confidence": 88,
                        "description": "OK",
                    },
                    {
                        "rule_id": "logo_effects",
                        "status": "NOT_APPLICABLE",
                        "confidence": 80,
                        "description": "N/A",
                    },
                    {
                        "rule_id": "content_separation",
                        "status": "PASS",
                        "confidence": 95,
                        "description": "OK",
                    },
                    {
                        "rule_id": "naming_pricing",
                        "status": "FAIL",
                        "confidence": 82,
                        "description": "Preço incorreto.",
                    },
                    {
                        "rule_id": "kv_integrity",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                ]
            }
        )

        detection_store = AsyncMock()
        detection_store.save = AsyncMock()

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=detection_store,
            storage_config=storage_config,
        )

        await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        # Apenas 2 FAIL → 2 chamadas ao save
        assert detection_store.save.call_count == 2
        match_types = [
            call.kwargs["detection"].match_type
            for call in detection_store.save.call_args_list
        ]
        assert "facilitator_role" in match_types
        assert "naming_pricing" in match_types


# --- Testes de CompliancePersistenceError após exaustão de retries ---


class TestPersistenceError:
    """Testa CompliancePersistenceError quando save falha após 3 tentativas."""

    @pytest.mark.asyncio
    async def test_persistence_error_apos_3_tentativas(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        storage_config: StorageConfig,
    ) -> None:
        """CompliancePersistenceError é levantado após 3 retries falharem."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "FAIL",
                        "confidence": 90,
                        "description": "Violação detectada.",
                    },
                    {
                        "rule_id": "logo_application",
                        "status": "PASS",
                        "confidence": 88,
                        "description": "OK",
                    },
                    {
                        "rule_id": "logo_effects",
                        "status": "PASS",
                        "confidence": 85,
                        "description": "OK",
                    },
                    {
                        "rule_id": "content_separation",
                        "status": "PASS",
                        "confidence": 95,
                        "description": "OK",
                    },
                    {
                        "rule_id": "naming_pricing",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                    {
                        "rule_id": "kv_integrity",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                ]
            }
        )

        # Mock que sempre falha
        detection_store = AsyncMock()
        detection_store.save = AsyncMock(
            side_effect=ConnectionError("Database unavailable")
        )

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=detection_store,
            storage_config=storage_config,
        )

        with patch("brand_watchdog.analyzer.compliance_analyzer.asyncio.sleep"):
            with pytest.raises(CompliancePersistenceError) as exc_info:
                await analyzer.analyze_compliance(
                    screenshot_path=Path("/tmp/screenshot.png"),
                    target_url="https://isp.com",
                    screenshot_ref_id="ref-001",
                    cycle_id="cycle-001",
                )

        assert "facilitator_role" in str(exc_info.value)
        assert "3 tentativas" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_persistence_error_tenta_3_vezes_antes_de_falhar(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        storage_config: StorageConfig,
    ) -> None:
        """DetectionStore.save é chamado 3 vezes antes de levantar erro."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "FAIL",
                        "confidence": 90,
                        "description": "Violação.",
                    },
                    {
                        "rule_id": "logo_application",
                        "status": "PASS",
                        "confidence": 88,
                        "description": "OK",
                    },
                    {
                        "rule_id": "logo_effects",
                        "status": "PASS",
                        "confidence": 85,
                        "description": "OK",
                    },
                    {
                        "rule_id": "content_separation",
                        "status": "PASS",
                        "confidence": 95,
                        "description": "OK",
                    },
                    {
                        "rule_id": "naming_pricing",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                    {
                        "rule_id": "kv_integrity",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                ]
            }
        )

        detection_store = AsyncMock()
        detection_store.save = AsyncMock(
            side_effect=RuntimeError("DB error")
        )

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=detection_store,
            storage_config=storage_config,
        )

        with patch("brand_watchdog.analyzer.compliance_analyzer.asyncio.sleep"):
            with pytest.raises(CompliancePersistenceError):
                await analyzer.analyze_compliance(
                    screenshot_path=Path("/tmp/screenshot.png"),
                    target_url="https://isp.com",
                    screenshot_ref_id="ref-001",
                    cycle_id="cycle-001",
                )

        # 3 tentativas para a única regra FAIL
        assert detection_store.save.call_count == 3

    @pytest.mark.asyncio
    async def test_persistence_sucesso_no_retry_nao_levanta_erro(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        storage_config: StorageConfig,
    ) -> None:
        """Se save tem sucesso no retry, não levanta erro."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value={
                "compliance_results": [
                    {
                        "rule_id": "facilitator_role",
                        "status": "FAIL",
                        "confidence": 90,
                        "description": "Violação.",
                    },
                    {
                        "rule_id": "logo_application",
                        "status": "PASS",
                        "confidence": 88,
                        "description": "OK",
                    },
                    {
                        "rule_id": "logo_effects",
                        "status": "PASS",
                        "confidence": 85,
                        "description": "OK",
                    },
                    {
                        "rule_id": "content_separation",
                        "status": "PASS",
                        "confidence": 95,
                        "description": "OK",
                    },
                    {
                        "rule_id": "naming_pricing",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                    {
                        "rule_id": "kv_integrity",
                        "status": "PASS",
                        "confidence": 90,
                        "description": "OK",
                    },
                ]
            }
        )

        # Falha na 1ª e 2ª tentativa, sucesso na 3ª
        detection_store = AsyncMock()
        detection_store.save = AsyncMock(
            side_effect=[
                RuntimeError("DB error"),
                RuntimeError("DB error"),
                "detection-id-ok",
            ]
        )

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=detection_store,
            storage_config=storage_config,
        )

        with patch("brand_watchdog.analyzer.compliance_analyzer.asyncio.sleep"):
            # Não deve levantar exceção
            report = await analyzer.analyze_compliance(
                screenshot_path=Path("/tmp/screenshot.png"),
                target_url="https://isp.com",
                screenshot_ref_id="ref-001",
                cycle_id="cycle-001",
            )

        assert report.overall_status == "non_compliant"
        assert detection_store.save.call_count == 3


# --- Teste: sem detection_store não persiste ---


class TestSemDetectionStore:
    """Testa que sem detection_store configurado não tenta persistir."""

    @pytest.mark.asyncio
    async def test_sem_detection_store_nao_persiste(
        self,
        analyzer_config: AnalyzerConfig,
        mock_prompt_builder: MagicMock,
        valid_bedrock_response: dict,
        storage_config: StorageConfig,
    ) -> None:
        """Sem detection_store, análise conclui sem persistência."""
        bedrock_client = AsyncMock()
        bedrock_client.invoke_model_multi = AsyncMock(
            return_value=valid_bedrock_response
        )

        analyzer = ComplianceAnalyzer(
            config=analyzer_config,
            bedrock_client=bedrock_client,
            prompt_builder=mock_prompt_builder,
            detection_store=None,
            storage_config=storage_config,
        )

        report = await analyzer.analyze_compliance(
            screenshot_path=Path("/tmp/screenshot.png"),
            target_url="https://isp.com",
            screenshot_ref_id="ref-001",
            cycle_id="cycle-001",
        )

        # Não levanta erro, retorna report normalmente
        assert report.overall_status == "non_compliant"
        assert len(report.rule_results) == 6
