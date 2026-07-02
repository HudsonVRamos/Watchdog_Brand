"""Analisador principal de compliance SKY+/Amazon Prime.

Orquestra o fluxo completo de validação de compliance:
1. Construção do prompt multimodal (PromptBuilder)
2. Invocação do modelo Bedrock (BedrockClient)
3. Parsing da resposta em ComplianceReport (ReportParser)
4. Persistência de violações como DetectionResult (DetectionStore)

Requisitos cobertos: 7.1, 7.2, 7.3, 7.4, 7.6, 7.7, 9.1, 9.2, 9.3, 9.4, 9.5
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from brand_watchdog.analyzer.bedrock_client import BedrockClient
from brand_watchdog.analyzer.compliance_exceptions import (
    AnalysisIncompleteError,
    CompliancePersistenceError,
)
from brand_watchdog.analyzer.compliance_prompt_builder import (
    CompliancePromptBuilder,
)
from brand_watchdog.analyzer.compliance_report_parser import (
    ComplianceReportParser,
)
from brand_watchdog.config import AnalyzerConfig, StorageConfig
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    ComplianceReport,
    DetectionResult,
)
from brand_watchdog.storage.detection_store import DetectionStore

logger = logging.getLogger(__name__)

# Constantes de retry para persistência
_PERSIST_MAX_ATTEMPTS = 3
_PERSIST_BASE_DELAY_SECONDS = 1.0


class ComplianceAnalyzer:
    """Analisador de compliance da parceria SKY+/Amazon Prime.

    Substitui o Analyzer original no fluxo de compliance,
    orquestrando a construção do prompt, invocação do Bedrock,
    parsing da resposta e persistência de violações.

    Args:
        config: Configuração do analisador (model_id, região, timeouts).
        bedrock_client: Cliente Bedrock para invocação do modelo.
            Se None, cria um novo com base na config.
        prompt_builder: Builder de prompt multimodal.
            Se None, cria um novo com config padrão.
        detection_store: Store para persistência de violações.
            Se None, violações não são persistidas.
        storage_config: Configuração de storage para cálculo de
            expires_at. Se None, usa defaults.
        brand: Tipo de brand para monitoramento
            ("sky_plus" ou "dgo"). Default: "sky_plus".
    """

    def __init__(
        self,
        config: AnalyzerConfig,
        bedrock_client: BedrockClient | None = None,
        prompt_builder: CompliancePromptBuilder | None = None,
        detection_store: DetectionStore | None = None,
        storage_config: StorageConfig | None = None,
        brand: str = "sky_plus",
    ) -> None:
        """Inicializa o ComplianceAnalyzer com dependências injetáveis."""
        self._config = config
        self._bedrock_client = bedrock_client or BedrockClient(config)
        self._prompt_builder = (
            prompt_builder
            or CompliancePromptBuilder(brand=brand)
        )
        self._parser = ComplianceReportParser()
        self._detection_store = detection_store
        self._storage_config = storage_config or StorageConfig()

    async def analyze_compliance(
        self,
        screenshot_path: Path,
        target_url: str,
        screenshot_ref_id: str,
        cycle_id: str,
        brand: str | None = None,
        target_site_id: str | None = None,
    ) -> ComplianceReport:
        """Executa análise completa de compliance para um screenshot.

        Fluxo:
            1. build_prompt(screenshot_path) → PromptPayload
            2. invoke_model_multi(payload.images, payload.prompt_text)
            3. parse_response(raw_json, ...) → ComplianceReport
            4. Para cada regra FAIL: persistir DetectionResult
            5. Retornar ComplianceReport

        Args:
            screenshot_path: Caminho para o screenshot do ISP.
            target_url: URL do site-alvo analisado.
            screenshot_ref_id: ID de referência do screenshot.
            cycle_id: ID do ciclo de monitoramento.
            brand: Brand override por chamada ("sky_plus" ou "dgo").
                Se None, usa o builder padrão configurado na init.
            target_site_id: UUID do target site no banco de dados.
                Se None, usa target_url como fallback.

        Returns:
            ComplianceReport com resultados de todas as regras.

        Raises:
            AnalysisIncompleteError: Se screenshot ilegível ou
                Bedrock retorna resposta não extraível.
            ComplianceParseError: Se resposta do Bedrock é inválida.
            CompliancePersistenceError: Se persistência falha após
                3 tentativas com backoff exponencial.
        """
        logger.debug(
            "Iniciando análise de compliance: url=%s, "
            "screenshot=%s, cycle=%s, brand=%s",
            target_url,
            screenshot_path,
            cycle_id,
            brand,
        )

        # Selecionar prompt builder: override por brand ou padrão
        if brand is not None:
            prompt_builder = CompliancePromptBuilder(brand=brand)
        else:
            prompt_builder = self._prompt_builder

        # 1. Construir prompt multimodal
        payload = prompt_builder.build_prompt(screenshot_path)

        # 2. Invocar modelo Bedrock
        try:
            raw_json = await self._bedrock_client.invoke_model_multi(
                payload.images, payload.prompt_text
            )
        except ValueError as e:
            # ValueError do _extract_json_from_response → converter
            raise AnalysisIncompleteError(
                f"Bedrock retornou resposta não extraível: {e}"
            ) from e

        # 3. Parsear resposta em ComplianceReport
        report = self._parser.parse_response(
            raw_json, target_url, screenshot_ref_id, cycle_id
        )

        logger.info(
            "Análise de compliance concluída: url=%s, status=%s, "
            "regras_fail=%d",
            target_url,
            report.overall_status,
            sum(
                1 for r in report.rule_results if r.status == "FAIL"
            ),
        )

        # 4. Persistir violações (regras FAIL)
        if self._detection_store is not None:
            await self._persist_violations(
                report, target_site_id or target_url, screenshot_ref_id, cycle_id
            )

        return report

    async def _persist_violations(
        self,
        report: ComplianceReport,
        target_url: str,
        screenshot_ref_id: str,
        cycle_id: str,
    ) -> None:
        """Persiste cada regra FAIL como DetectionResult.

        Para cada regra com status "FAIL", cria um DetectionResult
        e persiste via DetectionStore com retry (3 tentativas,
        backoff exponencial: 1s, 2s, 4s).

        Args:
            report: ComplianceReport com resultados das regras.
            target_url: URL do site-alvo.
            screenshot_ref_id: ID de referência do screenshot.
            cycle_id: ID do ciclo de monitoramento.

        Raises:
            CompliancePersistenceError: Se qualquer violação falha
                ao persistir após todas as tentativas.
        """
        fail_rules = [
            r for r in report.rule_results if r.status == "FAIL"
        ]

        if not fail_rules:
            logger.debug(
                "Nenhuma violação para persistir: url=%s", target_url
            )
            return

        retention_days = self._storage_config.detection_retention_days

        for rule in fail_rules:
            detection = DetectionResult(
                target_url=target_url,
                match_type=rule.rule_id,
                confidence=rule.confidence,
                bounding_box=BoundingBox(
                    x_percent=0.0,
                    y_percent=0.0,
                    width_percent=0.0,
                    height_percent=0.0,
                ),
                description=rule.description,
                detected_at=report.analyzed_at,
                screenshot_ref_id=screenshot_ref_id,
            )

            await self._save_with_retry(
                detection, target_url, cycle_id, retention_days
            )

    async def _save_with_retry(
        self,
        detection: DetectionResult,
        target_url: str,
        cycle_id: str,
        retention_days: int,
    ) -> None:
        """Persiste uma detecção com retry e backoff exponencial.

        Tenta 3 vezes com delays de 1s, 2s, 4s entre tentativas.
        Raise CompliancePersistenceError se todas falharem.

        Args:
            detection: DetectionResult a ser persistido.
            target_url: URL do site-alvo (para logging).
            cycle_id: ID do ciclo de monitoramento.
            retention_days: Dias de retenção para calcular expires_at.

        Raises:
            CompliancePersistenceError: Se todas as tentativas falharem.
        """
        last_exception: Exception | None = None

        for attempt in range(_PERSIST_MAX_ATTEMPTS):
            try:
                await self._detection_store.save(  # type: ignore[union-attr]
                    detection=detection,
                    target_site_id=target_url,
                    monitoring_cycle_id=cycle_id,
                )
                logger.debug(
                    "Violação persistida: rule=%s, url=%s, "
                    "tentativa=%d",
                    detection.match_type,
                    target_url,
                    attempt + 1,
                )
                return
            except Exception as e:
                last_exception = e
                delay = _PERSIST_BASE_DELAY_SECONDS * (2 ** attempt)
                logger.warning(
                    "Falha ao persistir violação (tentativa %d/%d): "
                    "rule=%s, url=%s, erro=%s. "
                    "Próxima tentativa em %.1fs.",
                    attempt + 1,
                    _PERSIST_MAX_ATTEMPTS,
                    detection.match_type,
                    target_url,
                    e,
                    delay,
                )
                if attempt < _PERSIST_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(delay)

        # Todas as tentativas falharam
        logger.error(
            "Falha ao persistir violação após %d tentativas: "
            "rule=%s, target=%s, cycle=%s",
            _PERSIST_MAX_ATTEMPTS,
            detection.match_type,
            target_url,
            cycle_id,
        )
        raise CompliancePersistenceError(
            f"Falha ao persistir violação '{detection.match_type}' "
            f"para target='{target_url}', cycle='{cycle_id}' "
            f"após {_PERSIST_MAX_ATTEMPTS} tentativas."
        ) from last_exception
