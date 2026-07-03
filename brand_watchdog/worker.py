"""Entry point do Worker ECS para processamento de sites.

Loop de consumo de mensagens SQS:
    receive → process → delete (1 mensagem por vez)

Fluxo por mensagem:
    1. Captura screenshot via Playwright/Chromium
    2. Upload do screenshot para S3 (ScreenshotStore)
    3. Análise de compliance via Bedrock (ComplianceAnalyzer)
    4. Persistência de resultados no banco de dados
    5. Publicação de evento ComplianceCompleted no EventBridge
    6. Deleção da mensagem SQS

Características:
    - Timeout de 120s por site com cleanup de Chromium
    - Renovação de visibility timeout a cada 60s
    - Registro de SiteCycleResult (sucesso ou falha) no banco
    - Falha de EventBridge NÃO impede conclusão
    - Cache de imagens de referência em memória por brand (Req 8.2)
    - Cleanup de cache ao final de cada ciclo

Requirements: 2.1, 2.3, 2.6, 2.7, 2.8, 3.4, 3.5, 8.2
"""

from __future__ import annotations

import asyncio
import io
import logging
import signal
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from brand_watchdog.analyzer.compliance_analyzer import ComplianceAnalyzer
from brand_watchdog.analyzer.compliance_prompt_builder import (
    BRAND_REFERENCE_IMAGES,
    CompliancePromptBuilder,
)
from brand_watchdog.analyzer.reference_image_cache import ReferenceImageCache
from brand_watchdog.config import (
    AppConfig,
    WorkerConfig,
    load_config,
)
from brand_watchdog.crawler.crawler import Crawler
from brand_watchdog.events.models import ComplianceCompletedEvent
from brand_watchdog.events.publisher import EventPublisher
from brand_watchdog.models.database import (
    get_session,
    setup_database,
    init_db,
    close_db,
)
from brand_watchdog.models.entities import SiteCycleResultModel
from brand_watchdog.queue.consumer import SQSConsumer
from brand_watchdog.queue.messages import ProcessingMessage
from brand_watchdog.storage.detection_store import DetectionStore
from brand_watchdog.storage.screenshot_store import ScreenshotStore

logger = logging.getLogger(__name__)


class WorkerMain:
    """Entry point do Worker ECS para processamento paralelo de sites.

    Consome mensagens da fila SQS uma por vez e executa o pipeline
    completo de processamento: captura → upload → análise → persist → evento.

    Integra todos os componentes:
    - SQSConsumer: recepção e gerenciamento de mensagens
    - Crawler: captura de screenshots via Chromium
    - ScreenshotStore: persistência em S3
    - ComplianceAnalyzer: análise via Bedrock
    - EventPublisher: publicação de eventos no EventBridge
    - ReferenceImageCache: cache de imagens de referência por brand

    Args:
        config: Configuração completa da aplicação.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._worker_config: WorkerConfig = config.worker
        self._running = True

        # Componentes (inicializados em setup)
        self._consumer: SQSConsumer | None = None
        self._crawler: Crawler | None = None
        self._screenshot_store: ScreenshotStore | None = None
        self._compliance_analyzer: ComplianceAnalyzer | None = None
        self._event_publisher: EventPublisher | None = None
        self._reference_cache: ReferenceImageCache | None = None
        self._prompt_builder: CompliancePromptBuilder | None = None

        # Controle de ciclo para limpeza de cache
        self._current_cycle_id: str | None = None

    async def setup(self) -> None:
        """Inicializa todos os componentes do worker.

        Configura banco de dados, SQS consumer, crawler, stores,
        analyzers, event publisher e reference cache necessários
        para o processamento.

        Componentes inicializados:
        - Banco de dados (engine + session factory)
        - SQSConsumer (fila de mensagens)
        - Crawler (Playwright/Chromium)
        - ScreenshotStore (upload S3 + metadados)
        - ComplianceAnalyzer (análise Bedrock)
        - CompliancePromptBuilder (construção de prompts)
        - EventPublisher (eventos EventBridge)
        - ReferenceImageCache (cache de imagens por brand)
        """
        # Banco de dados
        setup_database(self._config.storage)
        await init_db()

        # SQS Consumer
        self._consumer = SQSConsumer(
            queue_url=self._config.queue.queue_url,
            visibility_timeout=self._config.queue.visibility_timeout_seconds,
            region="us-east-1",
        )

        # Crawler (Playwright/Chromium)
        self._crawler = Crawler(
            config=self._config.crawler,
            storage_config=self._config.storage,
        )

        # Screenshot Store
        self._screenshot_store = ScreenshotStore(
            config=self._config.storage,
        )

        # Compliance Analyzer (com DetectionStore para persistir violações)
        detection_store = DetectionStore()
        self._compliance_analyzer = ComplianceAnalyzer(
            config=self._config.analyzer,
            storage_config=self._config.storage,
            detection_store=detection_store,
        )

        # Prompt Builder (para build_prompt_cached com referências)
        self._prompt_builder = CompliancePromptBuilder(
            brand=self._config.brand,
        )

        # Event Publisher
        self._event_publisher = EventPublisher(
            config=self._config.event,
        )

        # Reference Image Cache
        self._reference_cache = ReferenceImageCache(
            max_size_px=self._config.cache.max_image_size_px,
            jpeg_quality=self._config.cache.jpeg_quality,
        )

        # Pre-carregar imagens de referência no cache
        self._preload_reference_images()

        logger.info(
            "Worker inicializado: queue_url=%s, "
            "processing_timeout=%ds, visibility_renew=%ds",
            self._config.queue.queue_url,
            self._worker_config.processing_timeout_seconds,
            self._worker_config.visibility_renew_interval_seconds,
        )

    async def shutdown(self) -> None:
        """Encerra componentes e libera recursos.

        Fecha browser Chromium, conexão com banco e limpa caches.
        Garante que todos os recursos são liberados mesmo em caso
        de erro parcial.
        """
        self._running = False

        if self._crawler is not None:
            try:
                await self._crawler.close()
                logger.info("Crawler encerrado")
            except Exception as exc:
                logger.warning(
                    "Erro ao encerrar crawler: %s", str(exc)
                )

        if self._reference_cache is not None:
            self._reference_cache.clear()
            logger.debug("Cache de referências limpo")

        try:
            await close_db()
        except Exception as exc:
            logger.warning(
                "Erro ao fechar banco de dados: %s", str(exc)
            )

        logger.info("Worker encerrado com sucesso")

    def _preload_reference_images(self) -> None:
        """Carrega imagens de referência no cache para cada brand.

        Itera sobre todos os brands configurados e carrega suas
        imagens de referência em memória, já redimensionadas e
        convertidas para JPEG. Isso evita releitura do disco
        a cada mensagem processada (Req 8.2).
        """
        assert self._reference_cache is not None

        images_dir = Path("watchdog_rules/SKY_Amazon_Imagens")

        for brand, images_map in BRAND_REFERENCE_IMAGES.items():
            loaded_count = 0
            for filename, label in images_map.items():
                image_path = images_dir / filename
                if not image_path.exists():
                    logger.warning(
                        "Imagem de referência não encontrada: "
                        "brand=%s, arquivo=%s",
                        brand,
                        image_path,
                    )
                    continue

                jpeg_bytes = self._reference_cache.load_and_resize(
                    image_path
                )
                if jpeg_bytes is not None:
                    self._reference_cache.cache_image(
                        brand, jpeg_bytes, label
                    )
                    loaded_count += 1

            logger.info(
                "Imagens de referência carregadas: "
                "brand=%s, total=%d/%d",
                brand,
                loaded_count,
                len(images_map),
            )

    async def run(self) -> None:
        """Loop principal de consumo de mensagens.

        Executa continuamente até receber sinal de parada:
            1. Recebe 1 mensagem da fila
            2. Processa com timeout de 120s
            3. Deleta mensagem em caso de sucesso
            4. Registra resultado (sucesso/falha) no banco
        """
        logger.info("Worker iniciando loop de consumo")

        while self._running:
            try:
                await self._consume_one()
            except Exception as exc:
                logger.error(
                    "Erro não tratado no loop de consumo: %s",
                    str(exc),
                )
                # Pausa breve antes de tentar novamente
                await asyncio.sleep(5)

    async def _consume_one(self) -> None:
        """Consome e processa uma única mensagem da fila.

        Se a fila estiver vazia, retorna sem ação (long polling
        do SQS já aguarda 20s por mensagem).
        """
        assert self._consumer is not None

        result = await self._consumer.receive_message()
        if result is None:
            return

        message, receipt_handle = result

        logger.info(
            "Processando mensagem: site_id=%s, cycle_id=%s, url=%s",
            message.site_id,
            message.cycle_id,
            message.url,
        )

        # Processar com timeout e renovação de visibility
        success = await self._process_with_timeout(
            message, receipt_handle
        )

        # Deletar mensagem somente em caso de sucesso
        if success:
            try:
                await self._consumer.delete_message(receipt_handle)
                logger.info(
                    "Mensagem deletada: site_id=%s, cycle_id=%s",
                    message.site_id,
                    message.cycle_id,
                )
            except Exception as exc:
                logger.error(
                    "Falha ao deletar mensagem: site_id=%s, "
                    "cycle_id=%s, erro=%s",
                    message.site_id,
                    message.cycle_id,
                    str(exc),
                )

    async def _process_with_timeout(
        self,
        message: ProcessingMessage,
        receipt_handle: str,
    ) -> bool:
        """Processa mensagem com timeout e renovação de visibility.

        Executa o processamento do site com timeout de 120s.
        Enquanto o processamento está em andamento, renova o
        visibility timeout a cada 60s para evitar reprocessamento.

        Em caso de timeout, encerra o browser Chromium para
        liberar recursos.

        Args:
            message: Mensagem de processamento recebida.
            receipt_handle: Handle SQS para operações na mensagem.

        Returns:
            True se o processamento foi concluído com sucesso.
        """
        timeout_seconds = (
            self._worker_config.processing_timeout_seconds
        )
        renew_interval = (
            self._worker_config.visibility_renew_interval_seconds
        )

        # Task de renovação de visibility
        renew_task = asyncio.create_task(
            self._renew_visibility_loop(
                receipt_handle, renew_interval
            )
        )

        try:
            # Executa processamento com timeout
            result = await asyncio.wait_for(
                self._process_site(message),
                timeout=timeout_seconds,
            )
            return result

        except asyncio.TimeoutError:
            logger.error(
                "Timeout de %ds excedido: site_id=%s, "
                "cycle_id=%s, url=%s",
                timeout_seconds,
                message.site_id,
                message.cycle_id,
                message.url,
            )

            # Cleanup do Chromium
            await self._cleanup_chromium()

            # Registrar falha no banco
            await self._register_result(
                message=message,
                status=SiteCycleResultModel.STATUS_FAILURE,
                detections_count=0,
                failure_reason=(
                    f"Timeout de {timeout_seconds}s excedido "
                    f"ao processar {message.url}"
                ),
            )
            return False

        except Exception as exc:
            logger.error(
                "Erro ao processar site: site_id=%s, "
                "cycle_id=%s, url=%s, erro=%s",
                message.site_id,
                message.cycle_id,
                message.url,
                str(exc),
            )

            # Registrar falha no banco
            await self._register_result(
                message=message,
                status=SiteCycleResultModel.STATUS_FAILURE,
                detections_count=0,
                failure_reason=str(exc)[:1024],
            )
            return False

        finally:
            # Cancelar task de renovação
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass

    async def _renew_visibility_loop(
        self,
        receipt_handle: str,
        interval_seconds: int,
    ) -> None:
        """Loop de renovação de visibility timeout.

        Renova o visibility timeout a cada interval_seconds enquanto
        o processamento estiver em andamento. Executa como task
        assíncrona paralela ao processamento.

        Args:
            receipt_handle: Handle SQS da mensagem.
            interval_seconds: Intervalo entre renovações (60s).
        """
        assert self._consumer is not None

        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self._consumer.extend_visibility(
                    receipt_handle,
                    additional_seconds=interval_seconds,
                )
                logger.debug(
                    "Visibility timeout renovado: "
                    "receipt_handle=%s, additional=%ds",
                    receipt_handle[:30],
                    interval_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "Falha ao renovar visibility timeout: %s",
                    str(exc),
                )

    async def _process_site(self, message: ProcessingMessage) -> bool:
        """Executa o pipeline completo de processamento de um site.

        Fluxo:
            1. Detecta mudança de ciclo e limpa/recarrega cache se necessário
            2. Captura screenshot via Chromium
            3. Upload para S3 via ScreenshotStore
            4. Análise de compliance via Bedrock (com imagens cacheadas)
            5. Persistência de resultado no banco
            6. Publicação de evento no EventBridge

        Args:
            message: Mensagem com dados do site a processar.

        Returns:
            True se o processamento foi concluído com sucesso.
        """
        assert self._crawler is not None
        assert self._screenshot_store is not None
        assert self._compliance_analyzer is not None
        assert self._event_publisher is not None
        assert self._reference_cache is not None

        # Detectar mudança de ciclo para limpar cache
        if (
            self._current_cycle_id is not None
            and self._current_cycle_id != message.cycle_id
        ):
            logger.info(
                "Novo ciclo detectado (%s → %s), "
                "limpando cache de referências",
                self._current_cycle_id,
                message.cycle_id,
            )
            self._reference_cache.clear()
            self._preload_reference_images()

        self._current_cycle_id = message.cycle_id

        # 1. Captura screenshot
        capture_result = await self._crawler.capture(message.url)

        if not capture_result.success:
            logger.error(
                "Falha na captura: site_id=%s, url=%s, erro=%s",
                message.site_id,
                message.url,
                capture_result.error_message,
            )
            await self._register_result(
                message=message,
                status=SiteCycleResultModel.STATUS_FAILURE,
                detections_count=0,
                failure_reason=(
                    capture_result.error_message or "Falha na captura"
                ),
            )
            return True  # Processamento concluído (com falha registrada)

        # 2. Upload para S3 (imagem original, sem resize)
        png_bytes = capture_result.screenshot_path.read_bytes()
        screenshot_model = await self._screenshot_store.store(
            png_bytes=png_bytes,
            target_site_id=message.site_id,
            cycle_id=message.cycle_id,
            height_px=capture_result.page_height_px,
            was_truncated=capture_result.was_truncated,
        )

        # 3. Resize do screenshot para respeitar limites do Bedrock
        #    (max 8000px de dimensão, max ~4.5MB de payload)
        analysis_bytes, is_jpeg = self._resize_for_bedrock(png_bytes)

        # Escrever imagem resized em arquivo temporário para o analyzer
        suffix = ".jpeg" if is_jpeg else ".png"
        tmp_screenshot_path = Path(
            tempfile.mktemp(suffix=suffix, prefix="bw_resize_")
        )
        try:
            tmp_screenshot_path.write_bytes(analysis_bytes)

            # 4. Análise de compliance via Bedrock
            #    Usa imagens cacheadas do ReferenceImageCache (Req 8.2)
            report = (
                await self._compliance_analyzer.analyze_compliance(
                    screenshot_path=tmp_screenshot_path,
                    target_url=message.url,
                    screenshot_ref_id=screenshot_model.id,
                    cycle_id=message.cycle_id,
                    brand=message.brand,
                    target_site_id=message.site_id,
                )
            )
        finally:
            # Cleanup do arquivo temporário
            try:
                tmp_screenshot_path.unlink(missing_ok=True)
            except OSError:
                pass

        # Contar detecções (regras FAIL)
        detections_count = sum(
            1 for r in report.rule_results if r.status == "FAIL"
        )

        # 4. Registrar resultado de sucesso no banco
        await self._register_result(
            message=message,
            status=SiteCycleResultModel.STATUS_SUCCESS,
            detections_count=detections_count,
            failure_reason=None,
        )

        # 5. Publicar evento no EventBridge (falha não impede conclusão)
        await self._publish_event(message, report, screenshot_model.s3_key)

        logger.info(
            "Site processado com sucesso: site_id=%s, "
            "cycle_id=%s, url=%s, detections=%d, "
            "overall_status=%s",
            message.site_id,
            message.cycle_id,
            message.url,
            detections_count,
            report.overall_status,
        )

        return True

    async def _register_result(
        self,
        message: ProcessingMessage,
        status: str,
        detections_count: int,
        failure_reason: str | None,
    ) -> None:
        """Registra SiteCycleResult no banco de dados.

        Persiste o resultado do processamento (sucesso ou falha)
        para que o CycleConsolidator possa consolidar o ciclo.
        Se o resultado já existir (duplicata site_id + cycle_id),
        ignora silenciosamente.

        Args:
            message: Mensagem de processamento com site_id e cycle_id.
            status: "success" ou "failure".
            detections_count: Número de detecções encontradas.
            failure_reason: Motivo da falha (None se sucesso).
        """
        try:
            async with get_session() as session:
                result_model = SiteCycleResultModel(
                    id=str(uuid.uuid4()),
                    site_id=message.site_id,
                    cycle_id=message.cycle_id,
                    status=status,
                    detections_count=detections_count,
                    failure_reason=failure_reason,
                    completed_at=datetime.now(timezone.utc),
                )
                session.add(result_model)

            logger.info(
                "SiteCycleResult registrado: site_id=%s, "
                "cycle_id=%s, status=%s, detections=%d",
                message.site_id,
                message.cycle_id,
                status,
                detections_count,
            )
        except Exception as exc:
            # Duplicata (UniqueViolation) — ignorar silenciosamente
            if "UniqueViolation" in str(exc) or "uq_site_cycle" in str(exc):
                logger.info(
                    "SiteCycleResult já existe (duplicata ignorada): "
                    "site_id=%s, cycle_id=%s",
                    message.site_id,
                    message.cycle_id,
                )
            else:
                logger.error(
                    "Falha ao registrar SiteCycleResult: "
                    "site_id=%s, cycle_id=%s, status=%s, erro=%s",
                    message.site_id,
                    message.cycle_id,
                    status,
                    str(exc),
                )

    async def _publish_event(
        self,
        message: ProcessingMessage,
        report,
        screenshot_s3_key: str,
    ) -> None:
        """Publica evento ComplianceCompleted no EventBridge.

        A falha de publicação NÃO impede a conclusão do processamento.

        Args:
            message: Mensagem de processamento com dados do site.
            report: ComplianceReport com resultados da análise.
            screenshot_s3_key: Chave S3 do screenshot.
        """
        assert self._event_publisher is not None

        event = ComplianceCompletedEvent(
            site_id=message.site_id,
            cycle_id=message.cycle_id,
            target_url=message.url,
            brand=message.brand,
            overall_status=report.overall_status,
            rule_results=[
                {
                    "rule_id": r.rule_id,
                    "status": r.status,
                    "confidence": r.confidence,
                }
                for r in report.rule_results
            ],
            screenshot_s3_key=screenshot_s3_key,
            analyzed_at=report.analyzed_at.isoformat(),
        )

        success = await self._event_publisher.publish_compliance_completed(
            event
        )

        if not success:
            logger.warning(
                "Evento ComplianceCompleted não publicado "
                "(processamento continua): site_id=%s, cycle_id=%s",
                message.site_id,
                message.cycle_id,
            )

    async def _cleanup_chromium(self) -> None:
        """Encerra e reinicializa o browser Chromium.

        Chamado após timeout para garantir que processos
        órfãos do browser são liberados.
        """
        if self._crawler is not None:
            try:
                await self._crawler.close()
                logger.info("Chromium encerrado após timeout")
            except Exception as exc:
                logger.warning(
                    "Erro ao encerrar Chromium: %s", str(exc)
                )

            # Reinicializar crawler para próxima mensagem
            self._crawler = Crawler(
                config=self._config.crawler,
                storage_config=self._config.storage,
            )

    def _resize_for_bedrock(self, png_bytes: bytes) -> tuple[bytes, bool]:
        """Redimensiona screenshot para respeitar limites do Bedrock.

        Limites da API Bedrock/Anthropic:
        - Dimensão máxima: 8000px (largura ou altura)
        - Tamanho máximo recomendado: ~4.5MB

        Se a imagem excede 8000px em qualquer dimensão, redimensiona
        proporcionalmente. Se o resultado ainda excede 4.5MB, converte
        para JPEG com quality=80 para reduzir tamanho.

        Args:
            png_bytes: Bytes originais do screenshot PNG.

        Returns:
            Tupla (bytes da imagem, is_jpeg) indicando se foi
            convertida para JPEG.
        """
        max_dim = 8000
        max_size_bytes = 4_500_000
        is_jpeg = False

        try:
            img = Image.open(io.BytesIO(png_bytes))
        except Exception as exc:
            logger.warning(
                "Não foi possível abrir imagem para resize: %s",
                str(exc),
            )
            return png_bytes, False

        # Redimensionar se dimensões excedem 8000px
        if img.width > max_dim or img.height > max_dim:
            ratio = min(max_dim / img.width, max_dim / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            logger.info(
                "Screenshot redimensionado para Bedrock: "
                "%dx%d → %dx%d (%d bytes)",
                img.width,
                img.height,
                new_size[0],
                new_size[1],
                len(png_bytes),
            )

        # Se ainda > 4.5MB, converter para JPEG com quality reduzida
        if len(png_bytes) > max_size_bytes:
            img = Image.open(io.BytesIO(png_bytes))
            if img.mode == "RGBA":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            png_bytes = buf.getvalue()
            is_jpeg = True
            logger.info(
                "Screenshot convertido para JPEG (quality=80) "
                "para Bedrock: %d bytes",
                len(png_bytes),
            )

        return png_bytes, is_jpeg


def _handle_signal(worker: WorkerMain) -> None:
    """Configura handlers de sinais para shutdown graceful."""
    def _signal_handler():
        logger.info("Sinal de término recebido, encerrando worker...")
        worker._running = False

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)


async def main() -> None:
    """Entry point assíncrono do Worker ECS.

    Carrega configuração, inicializa componentes, configura
    signal handlers e inicia o loop de consumo.

    Garante que shutdown() é sempre chamado, mesmo em caso
    de erro durante setup ou execução.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Iniciando Brand Watchdog Worker ECS")

    config = load_config()
    worker = WorkerMain(config)

    try:
        await worker.setup()

        # Configura signal handlers (Unix only)
        try:
            _handle_signal(worker)
        except NotImplementedError:
            # Windows não suporta add_signal_handler
            pass

        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrompido pelo usuário")
    except Exception as exc:
        logger.critical(
            "Erro fatal no Worker: %s", str(exc), exc_info=True
        )
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
