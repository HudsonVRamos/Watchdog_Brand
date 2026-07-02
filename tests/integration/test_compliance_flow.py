"""Testes de integração para o fluxo completo de compliance.

Exercita o pipeline real: capture → analyze → persist → notify,
mockando apenas os serviços EXTERNOS (boto3/Bedrock, filesystem,
email provider). O ComplianceAnalyzer e ComplianceEmailNotifier
são instanciados reais, validando a integração entre componentes.

Requirements: 1.1, 7.1, 8.1, 9.1
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.alerts.compliance_email_notifier import (
    ComplianceEmailNotifier,
)
from brand_watchdog.analyzer.bedrock_client import BedrockClient
from brand_watchdog.analyzer.compliance_analyzer import ComplianceAnalyzer
from brand_watchdog.config import (
    AlertConfig,
    AnalyzerConfig,
    AppConfig,
    CrawlerConfig,
    StorageConfig,
)
from brand_watchdog.coordinator.coordinator import MonitoringCoordinator
from brand_watchdog.crawler.crawler import Crawler
from brand_watchdog.models.dataclasses import (
    CaptureResult,
    ComplianceReport,
    TargetSite,
)
from brand_watchdog.registry.target_site_manager import TargetSiteManager
from brand_watchdog.storage.detection_store import DetectionStore
from brand_watchdog.storage.screenshot_store import ScreenshotStore


# --- Resposta mock do Bedrock (todas regras PASS) ---

BEDROCK_RESPONSE_ALL_PASS = {
    "compliance_results": [
        {
            "rule_id": "facilitator_role",
            "status": "PASS",
            "confidence": 92,
            "description": "SKY+ referenciado corretamente em todas as menções.",
        },
        {
            "rule_id": "logo_application",
            "status": "PASS",
            "confidence": 88,
            "description": "Logos em ordem correta com separadores.",
        },
        {
            "rule_id": "logo_effects",
            "status": "PASS",
            "confidence": 90,
            "description": "Sem efeitos visuais indevidos nos logos.",
        },
        {
            "rule_id": "content_separation",
            "status": "PASS",
            "confidence": 85,
            "description": "Conteúdo parceiro visualmente separado.",
        },
        {
            "rule_id": "naming_pricing",
            "status": "PASS",
            "confidence": 95,
            "description": "Nomenclatura e preços corretos.",
        },
        {
            "rule_id": "kv_integrity",
            "status": "PASS",
            "confidence": 91,
            "description": "Key Visual íntegro sem alterações.",
        },
    ]
}


# --- Resposta mock do Bedrock (com violações FAIL) ---

BEDROCK_RESPONSE_WITH_FAILURES = {
    "compliance_results": [
        {
            "rule_id": "facilitator_role",
            "status": "FAIL",
            "confidence": 87,
            "description": "Amazon Prime mencionado sem referência ao SKY+.",
        },
        {
            "rule_id": "logo_application",
            "status": "FAIL",
            "confidence": 82,
            "description": "Logo Amazon Music aparece antes do SKY+.",
        },
        {
            "rule_id": "logo_effects",
            "status": "PASS",
            "confidence": 90,
            "description": "Sem efeitos visuais indevidos.",
        },
        {
            "rule_id": "content_separation",
            "status": "PASS",
            "confidence": 85,
            "description": "Conteúdo separado adequadamente.",
        },
        {
            "rule_id": "naming_pricing",
            "status": "PASS",
            "confidence": 95,
            "description": "Preços e nomenclatura OK.",
        },
        {
            "rule_id": "kv_integrity",
            "status": "PASS",
            "confidence": 91,
            "description": "KV sem alterações detectadas.",
        },
    ]
}


# --- Helpers ---


def _make_bedrock_raw_response(payload: dict) -> dict:
    """Simula a resposta bruta do Bedrock (content[0].text → JSON)."""
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}]
    }


def _make_boto3_invoke_response(payload: dict) -> dict:
    """Simula retorno do boto3 invoke_model (body como StreamingBody)."""
    body_content = json.dumps(
        {"content": [{"type": "text", "text": json.dumps(payload)}]}
    ).encode()
    body_mock = MagicMock()
    body_mock.read.return_value = body_content
    return {"body": body_mock}


def _fake_screenshot_bytes() -> bytes:
    """Retorna bytes PNG mínimos para testes."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _make_target_sites(count: int = 2) -> list[TargetSite]:
    """Cria lista de TargetSites fake para testes."""
    sites = []
    for i in range(count):
        sites.append(
            TargetSite(
                id=str(uuid.uuid4()),
                url=f"https://isp-site-{i+1}.com.br/sky-amazon",
                normalized_url=f"https://isp-site-{i+1}.com.br/sky-amazon",
                created_at=datetime.now(timezone.utc),
                active=True,
            )
        )
    return sites


# --- Fixtures ---


@pytest.fixture
def analyzer_config() -> AnalyzerConfig:
    """Configuração do analyzer para testes."""
    return AnalyzerConfig(
        bedrock_model_id="anthropic.claude-sonnet-4-6",
        bedrock_region="us-east-1",
        confidence_threshold=70,
        request_timeout_seconds=60,
        max_retries=3,
        retry_base_delay_seconds=0.01,
    )


@pytest.fixture
def alert_config() -> AlertConfig:
    """Configuração de alertas para testes."""
    return AlertConfig(
        provider="ses",
        ses_sender="compliance@brand-watchdog.com",
        recipients=["analyst@empresa.com", "legal@empresa.com"],
        retry_attempts=1,
        retry_interval_seconds=0,
    )


@pytest.fixture
def storage_config(tmp_path: Path) -> StorageConfig:
    """Configuração de storage para testes."""
    return StorageConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        screenshot_base_path=tmp_path / "screenshots",
        screenshot_retention_days=90,
        detection_retention_days=90,
    )


@pytest.fixture
def app_config(alert_config: AlertConfig, storage_config: StorageConfig) -> AppConfig:
    """AppConfig completo para testes de integração."""
    return AppConfig(
        crawler=CrawlerConfig(),
        analyzer=AnalyzerConfig(),
        alert=alert_config,
        storage=storage_config,
    )


@pytest.fixture
def mock_email_provider() -> AsyncMock:
    """Mock do EmailProvider (serviço externo SES/SMTP)."""
    provider = AsyncMock()
    provider.send = AsyncMock(return_value=None)
    return provider


@pytest.fixture
def mock_crawler(tmp_path: Path) -> AsyncMock:
    """Mock do Crawler que retorna CaptureResult com screenshot fake."""
    crawler = AsyncMock(spec=Crawler)
    fake_png = _fake_screenshot_bytes()

    async def capture_side_effect(target_url: str) -> CaptureResult:
        ref_id = str(uuid.uuid4())
        path = tmp_path / f"{ref_id}.png"
        path.write_bytes(fake_png)
        return CaptureResult(
            target_url=target_url,
            screenshot_path=path,
            screenshot_ref_id=ref_id,
            captured_at=datetime.now(timezone.utc),
            page_height_px=3000,
            was_truncated=False,
            success=True,
        )

    crawler.capture = AsyncMock(side_effect=capture_side_effect)
    return crawler


@pytest.fixture
def mock_detection_store() -> AsyncMock:
    """Mock do DetectionStore (evita banco de dados real)."""
    store = AsyncMock(spec=DetectionStore)
    store.save = AsyncMock(return_value=None)
    return store


@pytest.fixture
def mock_screenshot_store() -> AsyncMock:
    """Mock do ScreenshotStore (evita filesystem real)."""
    store = AsyncMock(spec=ScreenshotStore)

    async def store_side_effect(
        png_bytes: bytes,
        target_site_id: str,
        cycle_id: str,
        height_px: int = 0,
        was_truncated: bool = False,
    ):
        model = MagicMock()
        model.id = str(uuid.uuid4())
        return model

    store.store = AsyncMock(side_effect=store_side_effect)
    return store


@pytest.fixture
def mock_target_site_manager() -> AsyncMock:
    """Mock do TargetSiteManager que retorna sites fake."""
    manager = AsyncMock(spec=TargetSiteManager)
    manager.list_all = AsyncMock(return_value=_make_target_sites(2))
    return manager


# --- Testes de Integração: Fluxo Completo de Compliance ---


@pytest.mark.integration
class TestComplianceFlowIntegration:
    """Testes de integração do fluxo capture → analyze → persist → notify.

    Usa o ComplianceAnalyzer REAL com boto3 mockado, exercitando
    a integração entre todos os componentes internos.
    """

    async def test_end_to_end_compliant_flow(
        self,
        analyzer_config: AnalyzerConfig,
        alert_config: AlertConfig,
        storage_config: StorageConfig,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_detection_store: AsyncMock,
        mock_screenshot_store: AsyncMock,
        mock_target_site_manager: AsyncMock,
        mock_email_provider: AsyncMock,
    ) -> None:
        """Fluxo completo end-to-end com resposta compliant do Bedrock.

        Verifica que o coordinator:
        1. Captura screenshots de todos os sites
        2. Invoca Bedrock via ComplianceAnalyzer (boto3 mockado)
        3. Não persiste violações (todas regras PASS)
        4. Envia relatório de compliance por email
        """
        # Configura mock do boto3 para retornar resposta compliant
        boto3_response = _make_boto3_invoke_response(BEDROCK_RESPONSE_ALL_PASS)

        with patch("brand_watchdog.analyzer.bedrock_client.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = boto3_response
            mock_boto3.client.return_value = mock_client

            # Cria ComplianceAnalyzer REAL com bedrock mockado
            bedrock_client = BedrockClient(analyzer_config)
            compliance_analyzer = ComplianceAnalyzer(
                config=analyzer_config,
                bedrock_client=bedrock_client,
                detection_store=mock_detection_store,
                storage_config=storage_config,
            )

            # Cria ComplianceEmailNotifier REAL com provider mockado
            compliance_notifier = ComplianceEmailNotifier(
                config=alert_config,
                email_provider=mock_email_provider,
            )

            coordinator = MonitoringCoordinator(
                crawler=mock_crawler,
                compliance_analyzer=compliance_analyzer,
                compliance_notifier=compliance_notifier,
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
                target_site_manager=mock_target_site_manager,
                config=app_config,
            )

            # Mocka métodos de banco do coordinator
            coordinator._create_cycle_record = AsyncMock()
            coordinator._update_cycle_record = AsyncMock()

            cycle_result = await coordinator.run_cycle()

        # Verificações
        # 1. Captura executada para 2 sites
        assert mock_crawler.capture.call_count == 2

        # 2. Bedrock invocado para cada site
        assert mock_client.invoke_model.call_count == 2

        # 3. Nenhuma violação persistida (todas PASS)
        mock_detection_store.save.assert_not_called()

        # 4. Email enviado para cada site (2 sites)
        assert mock_email_provider.send.call_count == 4  # 2 sites × 2 recipients

        # 5. CycleResult correto
        assert cycle_result.sites_processed == 2
        assert cycle_result.sites_failed == 0
        assert len(cycle_result.site_results) == 2
        for sr in cycle_result.site_results:
            assert sr.success is True

    async def test_end_to_end_non_compliant_persists_violations(
        self,
        analyzer_config: AnalyzerConfig,
        alert_config: AlertConfig,
        storage_config: StorageConfig,
        app_config: AppConfig,
        mock_crawler: AsyncMock,
        mock_detection_store: AsyncMock,
        mock_screenshot_store: AsyncMock,
        mock_target_site_manager: AsyncMock,
        mock_email_provider: AsyncMock,
    ) -> None:
        """Fluxo com regras FAIL: verifica persistência de violações.

        Verifica que:
        - DetectionStore.save é chamado para cada regra FAIL
        - Email é enviado mesmo com status non_compliant
        """
        boto3_response = _make_boto3_invoke_response(
            BEDROCK_RESPONSE_WITH_FAILURES
        )

        with patch("brand_watchdog.analyzer.bedrock_client.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = boto3_response
            mock_boto3.client.return_value = mock_client

            bedrock_client = BedrockClient(analyzer_config)
            compliance_analyzer = ComplianceAnalyzer(
                config=analyzer_config,
                bedrock_client=bedrock_client,
                detection_store=mock_detection_store,
                storage_config=storage_config,
            )

            compliance_notifier = ComplianceEmailNotifier(
                config=alert_config,
                email_provider=mock_email_provider,
            )

            coordinator = MonitoringCoordinator(
                crawler=mock_crawler,
                compliance_analyzer=compliance_analyzer,
                compliance_notifier=compliance_notifier,
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
                target_site_manager=mock_target_site_manager,
                config=app_config,
            )

            coordinator._create_cycle_record = AsyncMock()
            coordinator._update_cycle_record = AsyncMock()

            cycle_result = await coordinator.run_cycle()

        # 2 regras FAIL × 2 sites = 4 chamadas ao DetectionStore.save
        assert mock_detection_store.save.call_count == 4

        # Verifica que os rule_ids persistidos estão corretos
        saved_rule_ids = [
            call.kwargs["detection"].match_type
            for call in mock_detection_store.save.call_args_list
        ]
        assert saved_rule_ids.count("facilitator_role") == 2
        assert saved_rule_ids.count("logo_application") == 2

        # Email enviado para todos os sites (mesmo non_compliant)
        assert mock_email_provider.send.call_count == 4  # 2 sites × 2 recipients
        assert cycle_result.sites_processed == 2


    async def test_bedrock_client_invoked_with_correct_payload(
        self,
        analyzer_config: AnalyzerConfig,
        storage_config: StorageConfig,
        mock_detection_store: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Verifica que invoke_model_multi recebe payload correto.

        Testa que o BedrockClient é chamado com:
        - modelId correto
        - body contendo imagens e prompt
        - contentType application/json
        """
        boto3_response = _make_boto3_invoke_response(BEDROCK_RESPONSE_ALL_PASS)

        with patch("brand_watchdog.analyzer.bedrock_client.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = boto3_response
            mock_boto3.client.return_value = mock_client

            bedrock_client = BedrockClient(analyzer_config)
            compliance_analyzer = ComplianceAnalyzer(
                config=analyzer_config,
                bedrock_client=bedrock_client,
                detection_store=mock_detection_store,
                storage_config=storage_config,
            )

            # Cria screenshot fake
            screenshot_path = tmp_path / "test_screenshot.png"
            screenshot_path.write_bytes(_fake_screenshot_bytes())

            report = await compliance_analyzer.analyze_compliance(
                screenshot_path=screenshot_path,
                target_url="https://isp-test.com.br/sky",
                screenshot_ref_id="ref-123",
                cycle_id="cycle-456",
            )

        # Verifica chamada ao boto3
        mock_client.invoke_model.assert_called_once()
        call_kwargs = mock_client.invoke_model.call_args.kwargs

        assert call_kwargs["modelId"] == "anthropic.claude-sonnet-4-6"
        assert call_kwargs["contentType"] == "application/json"
        assert call_kwargs["accept"] == "application/json"

        # Verifica body do payload
        body = json.loads(call_kwargs["body"])
        assert body["anthropic_version"] == "bedrock-2023-05-31"
        assert body["max_tokens"] == 4096
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

        # Content deve conter imagens (text+image blocks) e prompt final
        content = body["messages"][0]["content"]
        assert len(content) >= 3  # pelo menos 1 label + 1 imagem + prompt

        # Primeiro block é label, segundo é imagem
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"
        assert content[1]["source"]["media_type"] == "image/png"

        # Último block é o prompt
        assert content[-1]["type"] == "text"
        assert "compliance" in content[-1]["text"].lower() or len(content[-1]["text"]) > 100

        # Report retornado é válido
        assert report.overall_status == "compliant"
        assert report.target_url == "https://isp-test.com.br/sky"
        assert len(report.rule_results) == 6


    async def test_email_sent_with_compliance_report(
        self,
        alert_config: AlertConfig,
        mock_email_provider: AsyncMock,
    ) -> None:
        """Verifica que send_compliance_report envia email com conteúdo correto.

        Testa o ComplianceEmailNotifier REAL com EmailProvider mockado:
        - Email é enviado para todos os recipients
        - Subject contém URL e status
        - Body contém regras, confidence e timestamp
        """
        notifier = ComplianceEmailNotifier(
            config=alert_config,
            email_provider=mock_email_provider,
        )

        report = ComplianceReport(
            target_url="https://isp-teste.com.br/amazon",
            analyzed_at=datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc),
            overall_status="non_compliant",
            rule_results=[
                MagicMock(
                    rule_id="facilitator_role",
                    status="FAIL",
                    confidence=87,
                    description="Amazon sem referência SKY+.",
                ),
                MagicMock(
                    rule_id="logo_application",
                    status="PASS",
                    confidence=92,
                    description="Logos corretos.",
                ),
            ],
            screenshot_ref_id="ss-ref-1",
            cycle_id="cycle-1",
        )

        result = await notifier.send_compliance_report(
            report=report,
            recipients=["analyst@empresa.com", "legal@empresa.com"],
        )

        assert result is True
        # 2 recipients = 2 envios
        assert mock_email_provider.send.call_count == 2

        # Verifica conteúdo do email
        first_call = mock_email_provider.send.call_args_list[0]
        call_kwargs = first_call.kwargs
        assert call_kwargs["recipient"] == "analyst@empresa.com"
        assert call_kwargs["sender"] == "compliance@brand-watchdog.com"
        assert "NON-COMPLIANT" in call_kwargs["subject"]
        assert "isp-teste.com.br" in call_kwargs["subject"]

        body = call_kwargs["body"]
        assert "isp-teste.com.br/amazon" in body
        assert "2024-06-15" in body
        assert "facilitator_role" in body
        assert "NON_COMPLIANT" in body

    async def test_error_isolation_one_site_fails_others_continue(
        self,
        analyzer_config: AnalyzerConfig,
        alert_config: AlertConfig,
        storage_config: StorageConfig,
        app_config: AppConfig,
        mock_detection_store: AsyncMock,
        mock_screenshot_store: AsyncMock,
        mock_email_provider: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Se um site falha, os demais continuam sendo processados.

        Simula falha no Bedrock para o primeiro site e resposta
        válida para o segundo. Verifica que:
        - Primeiro site retorna success=False
        - Segundo site é processado normalmente
        - Email enviado apenas para o site com sucesso
        """
        sites = _make_target_sites(2)
        target_site_manager = AsyncMock(spec=TargetSiteManager)
        target_site_manager.list_all = AsyncMock(return_value=sites)

        # Crawler que funciona para ambos os sites
        fake_png = _fake_screenshot_bytes()
        crawler = AsyncMock(spec=Crawler)

        async def capture_ok(target_url: str) -> CaptureResult:
            ref_id = str(uuid.uuid4())
            path = tmp_path / f"{ref_id}.png"
            path.write_bytes(fake_png)
            return CaptureResult(
                target_url=target_url,
                screenshot_path=path,
                screenshot_ref_id=ref_id,
                captured_at=datetime.now(timezone.utc),
                page_height_px=2500,
                was_truncated=False,
                success=True,
            )

        crawler.capture = AsyncMock(side_effect=capture_ok)

        # Bedrock falha para o primeiro site (todas tentativas),
        # sucesso para o segundo site.
        # O retry do tenacity faz 3 tentativas — precisamos falhar todas 3
        # para o site 1 e retornar ok para o site 2.
        boto3_response_ok = _make_boto3_invoke_response(BEDROCK_RESPONSE_ALL_PASS)
        call_count = {"n": 0}

        def invoke_model_side_effect(**kwargs):
            from botocore.exceptions import ClientError

            call_count["n"] += 1
            # Primeiras 3 chamadas são retries do site 1 → todas falham
            if call_count["n"] <= 3:
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                    "InvokeModel",
                )
            # Chamadas seguintes (site 2) → sucesso
            return boto3_response_ok

        with patch("brand_watchdog.analyzer.bedrock_client.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_client.invoke_model.side_effect = invoke_model_side_effect
            mock_boto3.client.return_value = mock_client

            bedrock_client = BedrockClient(analyzer_config)
            compliance_analyzer = ComplianceAnalyzer(
                config=analyzer_config,
                bedrock_client=bedrock_client,
                detection_store=mock_detection_store,
                storage_config=storage_config,
            )

            compliance_notifier = ComplianceEmailNotifier(
                config=alert_config,
                email_provider=mock_email_provider,
            )

            coordinator = MonitoringCoordinator(
                crawler=crawler,
                compliance_analyzer=compliance_analyzer,
                compliance_notifier=compliance_notifier,
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
                target_site_manager=target_site_manager,
                config=app_config,
            )

            coordinator._create_cycle_record = AsyncMock()
            coordinator._update_cycle_record = AsyncMock()

            cycle_result = await coordinator.run_cycle()

        # Primeiro site falhou, segundo teve sucesso
        assert cycle_result.sites_processed == 1
        assert cycle_result.sites_failed == 1
        assert len(cycle_result.site_results) == 2

        # Verifica isolamento: site com falha e site com sucesso
        failed_sites = [sr for sr in cycle_result.site_results if not sr.success]
        success_sites = [sr for sr in cycle_result.site_results if sr.success]
        assert len(failed_sites) == 1
        assert len(success_sites) == 1
        assert failed_sites[0].error_message is not None

        # Email enviado apenas para o site com sucesso (2 recipients)
        assert mock_email_provider.send.call_count == 2


    async def test_detection_store_save_called_with_correct_fields(
        self,
        analyzer_config: AnalyzerConfig,
        storage_config: StorageConfig,
        mock_detection_store: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Verifica os campos do DetectionResult persistido para regras FAIL.

        Para cada regra FAIL, o DetectionStore.save deve receber:
        - detection.match_type = rule_id
        - detection.confidence = confidence da regra
        - detection.bounding_box com zeros
        - detection.description = description da regra
        """
        boto3_response = _make_boto3_invoke_response(
            BEDROCK_RESPONSE_WITH_FAILURES
        )

        with patch("brand_watchdog.analyzer.bedrock_client.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_client.invoke_model.return_value = boto3_response
            mock_boto3.client.return_value = mock_client

            bedrock_client = BedrockClient(analyzer_config)
            compliance_analyzer = ComplianceAnalyzer(
                config=analyzer_config,
                bedrock_client=bedrock_client,
                detection_store=mock_detection_store,
                storage_config=storage_config,
            )

            screenshot_path = tmp_path / "test.png"
            screenshot_path.write_bytes(_fake_screenshot_bytes())

            await compliance_analyzer.analyze_compliance(
                screenshot_path=screenshot_path,
                target_url="https://isp-test.com.br",
                screenshot_ref_id="ss-ref-99",
                cycle_id="cycle-99",
            )

        # 2 regras FAIL → 2 chamadas ao save
        assert mock_detection_store.save.call_count == 2

        # Verifica primeira violação (facilitator_role)
        first_call = mock_detection_store.save.call_args_list[0]
        detection_1 = first_call.kwargs["detection"]
        assert detection_1.match_type == "facilitator_role"
        assert detection_1.confidence == 87
        assert detection_1.bounding_box.x_percent == 0.0
        assert detection_1.bounding_box.y_percent == 0.0
        assert detection_1.bounding_box.width_percent == 0.0
        assert detection_1.bounding_box.height_percent == 0.0
        assert "Amazon Prime" in detection_1.description or "SKY+" in detection_1.description
        assert first_call.kwargs["target_site_id"] == "https://isp-test.com.br"
        assert first_call.kwargs["monitoring_cycle_id"] == "cycle-99"

        # Verifica segunda violação (logo_application)
        second_call = mock_detection_store.save.call_args_list[1]
        detection_2 = second_call.kwargs["detection"]
        assert detection_2.match_type == "logo_application"
        assert detection_2.confidence == 82
        assert detection_2.bounding_box.x_percent == 0.0
