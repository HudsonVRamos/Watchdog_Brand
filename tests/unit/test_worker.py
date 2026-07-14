"""Testes unitários para WorkerMain.

Valida:
- Timeout de 120s com cleanup de Chromium e registro de falha no DB
- Renovação de visibility timeout a cada 60s
- EventBridge falha não impede conclusão
- Registro de SiteCycleResult no banco (sucesso e falha)
- Fluxo completo: captura → upload → análise → persistência → evento
- Worker processa 1 mensagem por vez (consume_one)

Requirements: 2.6, 2.7, 2.8, 3.4, 3.5, 5.4
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

from brand_watchdog.config import AppConfig
from brand_watchdog.models.dataclasses import (
    CaptureResult,
    ComplianceReport,
    ComplianceRuleResult,
)
from brand_watchdog.queue.messages import ProcessingMessage
from brand_watchdog.worker import WorkerMain


def _make_config() -> AppConfig:
    """Cria configuração de teste com timeouts reduzidos."""
    config = AppConfig()
    config.worker.processing_timeout_seconds = 2
    config.worker.visibility_renew_interval_seconds = 1
    config.queue.queue_url = (
        "https://sqs.us-east-1.amazonaws.com/123456/test-queue"
    )
    return config


def _make_message() -> ProcessingMessage:
    """Cria mensagem de processamento de teste."""
    return ProcessingMessage(
        site_id="site-001",
        cycle_id="cycle-001",
        brand="sky_plus",
        url="https://example.com/partner",
        rule_set_version="v1719849600_a3b2c1d4",
    )


def _make_capture_result(success: bool = True) -> CaptureResult:
    """Cria resultado de captura de teste."""
    return CaptureResult(
        target_url="https://example.com/partner",
        screenshot_path=Path("/tmp/screenshot.png"),
        screenshot_ref_id="screenshot-001",
        captured_at=datetime.now(timezone.utc),
        page_height_px=5000,
        was_truncated=False,
        success=success,
        error_message=None if success else "Timeout ao carregar",
    )


def _make_compliance_report() -> ComplianceReport:
    """Cria relatório de compliance de teste."""
    return ComplianceReport(
        target_url="https://example.com/partner",
        analyzed_at=datetime.now(timezone.utc),
        overall_status="non_compliant",
        rule_results=[
            ComplianceRuleResult(
                rule_id="facilitator_role",
                status="PASS",
                confidence=92,
                description="Papel de facilitador presente.",
            ),
            ComplianceRuleResult(
                rule_id="content_separation",
                status="FAIL",
                confidence=85,
                description="Separação inadequada de conteúdo.",
            ),
        ],
        screenshot_ref_id="screenshot-001",
        cycle_id="cycle-001",
    )


def _make_screenshot_model() -> MagicMock:
    """Cria modelo de screenshot de teste."""
    model = MagicMock()
    model.id = "screenshot-001"
    model.s3_key = "screenshots/cycle-001/screenshot-001.png"
    return model


class TestProcessWithTimeout:
    """Testes para _process_with_timeout."""

    @pytest.mark.asyncio
    async def test_timeout_registers_failure_and_cleans_chromium(self):
        """Timeout de 120s registra falha no DB e encerra Chromium."""
        config = _make_config()
        # Timeout muito curto para forçar timeout
        config.worker.processing_timeout_seconds = 0.1
        config.worker.visibility_renew_interval_seconds = 10
        worker = WorkerMain(config)

        # Mock do consumer para extend_visibility
        mock_consumer = AsyncMock()
        worker._consumer = mock_consumer

        # Mock do crawler que demora mais que o timeout
        async def slow_capture(url):
            await asyncio.sleep(10)
            return _make_capture_result()

        mock_crawler = MagicMock()
        mock_crawler.capture = slow_capture
        mock_crawler.close = AsyncMock()
        worker._crawler = mock_crawler

        # Mock de outros componentes necessários
        worker._screenshot_store = AsyncMock()
        worker._compliance_analyzer = AsyncMock()
        worker._event_publisher = AsyncMock()
        worker._reference_cache = MagicMock()

        message = _make_message()

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            result = await worker._process_with_timeout(
                message, "receipt-handle-123"
            )

        # Deve retornar falha
        assert result is False
        # Deve ter encerrado o Chromium
        mock_crawler.close.assert_called_once()
        # Deve ter registrado falha no banco de dados
        mock_session.add.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.status == "failure"
        assert added_model.site_id == "site-001"
        assert added_model.cycle_id == "cycle-001"
        assert "Timeout" in added_model.failure_reason

    @pytest.mark.asyncio
    async def test_visibility_renewed_during_processing(self):
        """Visibility timeout é renovado a cada 60s durante processamento."""
        config = _make_config()
        config.worker.processing_timeout_seconds = 3
        config.worker.visibility_renew_interval_seconds = 1
        worker = WorkerMain(config)

        # Mock do consumer
        mock_consumer = AsyncMock()
        worker._consumer = mock_consumer

        # Mock do crawler que demora ~2.5s (permite 2 renovações)
        async def slow_capture(url):
            await asyncio.sleep(2.5)
            return _make_capture_result()

        mock_crawler = AsyncMock()
        mock_crawler.capture = slow_capture
        mock_crawler.close = AsyncMock()
        worker._crawler = mock_crawler

        # Mock screenshot store
        mock_store = AsyncMock()
        mock_store.store = AsyncMock(
            return_value=_make_screenshot_model()
        )
        worker._screenshot_store = mock_store

        # Mock compliance analyzer
        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_compliance = AsyncMock(
            return_value=_make_compliance_report()
        )
        worker._compliance_analyzer = mock_analyzer

        # Mock event publisher
        mock_publisher = AsyncMock()
        mock_publisher.publish_compliance_completed = AsyncMock(
            return_value=True
        )
        worker._event_publisher = mock_publisher

        # Mock reference cache
        worker._reference_cache = MagicMock()
        worker._reference_cache.get_cached_images = MagicMock(
            return_value=[]
        )

        message = _make_message()

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            with patch.object(
                Path, "read_bytes", return_value=b"PNG_BYTES"
            ):
                result = await worker._process_with_timeout(
                    message, "receipt-handle-123"
                )

        # Processamento deve ter completado com sucesso
        assert result is True
        # Visibility timeout deve ter sido renovado pelo menos 2 vezes
        assert mock_consumer.extend_visibility.call_count >= 2


class TestProcessSite:
    """Testes para _process_site (pipeline completo)."""

    @pytest.mark.asyncio
    async def test_eventbridge_failure_does_not_block_completion(self):
        """Falha do EventBridge não impede conclusão."""
        config = _make_config()
        worker = WorkerMain(config)

        # Mock crawler
        mock_crawler = AsyncMock()
        mock_crawler.capture = AsyncMock(
            return_value=_make_capture_result()
        )
        worker._crawler = mock_crawler

        # Mock screenshot store
        mock_store = AsyncMock()
        mock_store.store = AsyncMock(
            return_value=_make_screenshot_model()
        )
        worker._screenshot_store = mock_store

        # Mock compliance analyzer
        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_compliance = AsyncMock(
            return_value=_make_compliance_report()
        )
        worker._compliance_analyzer = mock_analyzer

        # Mock event publisher que FALHA
        mock_publisher = AsyncMock()
        mock_publisher.publish_compliance_completed = AsyncMock(
            return_value=False
        )
        worker._event_publisher = mock_publisher

        # Mock reference cache
        worker._reference_cache = MagicMock()
        worker._reference_cache.get_cached_images = MagicMock(
            return_value=[]
        )
        worker._reference_cache.clear = MagicMock()

        message = _make_message()

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            with patch.object(
                Path, "read_bytes", return_value=b"PNG_BYTES"
            ):
                result = await worker._process_site(message)

        # Deve ter completado com sucesso mesmo com EventBridge falhando
        assert result is True
        # Event publisher foi chamado
        mock_publisher.publish_compliance_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_capture_failure_registers_failure_result(self):
        """Falha na captura registra resultado de falha."""
        config = _make_config()
        worker = WorkerMain(config)

        # Mock crawler que falha
        mock_crawler = AsyncMock()
        mock_crawler.capture = AsyncMock(
            return_value=_make_capture_result(success=False)
        )
        worker._crawler = mock_crawler
        worker._screenshot_store = AsyncMock()
        worker._compliance_analyzer = AsyncMock()
        worker._event_publisher = AsyncMock()
        worker._reference_cache = MagicMock()
        worker._reference_cache.get_cached_images = MagicMock(
            return_value=[]
        )
        worker._reference_cache.clear = MagicMock()

        message = _make_message()

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            result = await worker._process_site(message)

        # Processamento retorna True (falha registrada, msg pode ser deletada)
        assert result is True
        # Verifica que resultado de falha foi registrado
        mock_session.add.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.status == "failure"
        assert added_model.site_id == "site-001"
        assert added_model.cycle_id == "cycle-001"

    @pytest.mark.asyncio
    async def test_success_registers_success_result_with_detections(self):
        """Processamento bem-sucedido registra resultado de sucesso."""
        config = _make_config()
        worker = WorkerMain(config)

        # Mock crawler
        mock_crawler = AsyncMock()
        mock_crawler.capture = AsyncMock(
            return_value=_make_capture_result()
        )
        worker._crawler = mock_crawler

        # Mock screenshot store
        mock_store = AsyncMock()
        mock_store.store = AsyncMock(
            return_value=_make_screenshot_model()
        )
        worker._screenshot_store = mock_store

        # Mock compliance analyzer
        report = _make_compliance_report()
        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_compliance = AsyncMock(
            return_value=report
        )
        worker._compliance_analyzer = mock_analyzer

        # Mock event publisher
        mock_publisher = AsyncMock()
        mock_publisher.publish_compliance_completed = AsyncMock(
            return_value=True
        )
        worker._event_publisher = mock_publisher

        # Mock reference cache
        worker._reference_cache = MagicMock()
        worker._reference_cache.get_cached_images = MagicMock(
            return_value=[]
        )
        worker._reference_cache.clear = MagicMock()

        message = _make_message()

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            with patch.object(
                Path, "read_bytes", return_value=b"PNG_BYTES"
            ):
                result = await worker._process_site(message)

        # Processamento deve ter completado
        assert result is True
        # Verifica resultado de sucesso registrado
        mock_session.add.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.status == "success"
        assert added_model.detections_count == 1  # 1 regra com FAIL


class TestConsumeOne:
    """Testes para _consume_one."""

    @pytest.mark.asyncio
    async def test_empty_queue_returns_without_action(self):
        """Fila vazia retorna sem processamento."""
        config = _make_config()
        worker = WorkerMain(config)

        mock_consumer = AsyncMock()
        mock_consumer.receive_message = AsyncMock(return_value=None)
        worker._consumer = mock_consumer

        await worker._consume_one()

        # Não deve ter tentado processar nada
        mock_consumer.delete_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_processing_deletes_message(self):
        """Processamento bem-sucedido deleta a mensagem."""
        config = _make_config()
        worker = WorkerMain(config)

        message = _make_message()
        mock_consumer = AsyncMock()
        mock_consumer.receive_message = AsyncMock(
            return_value=(message, "receipt-handle-123")
        )
        mock_consumer.delete_message = AsyncMock()
        mock_consumer.extend_visibility = AsyncMock()
        worker._consumer = mock_consumer

        # Mock crawler
        mock_crawler = AsyncMock()
        mock_crawler.capture = AsyncMock(
            return_value=_make_capture_result()
        )
        mock_crawler.close = AsyncMock()
        worker._crawler = mock_crawler

        # Mock screenshot store
        mock_store = AsyncMock()
        mock_store.store = AsyncMock(
            return_value=_make_screenshot_model()
        )
        worker._screenshot_store = mock_store

        # Mock compliance analyzer
        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_compliance = AsyncMock(
            return_value=_make_compliance_report()
        )
        worker._compliance_analyzer = mock_analyzer

        # Mock event publisher
        mock_publisher = AsyncMock()
        mock_publisher.publish_compliance_completed = AsyncMock(
            return_value=True
        )
        worker._event_publisher = mock_publisher

        # Mock reference cache
        worker._reference_cache = MagicMock()
        worker._reference_cache.get_cached_images = MagicMock(
            return_value=[]
        )
        worker._reference_cache.clear = MagicMock()

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            with patch.object(
                Path, "read_bytes", return_value=b"PNG_BYTES"
            ):
                await worker._consume_one()

        # Deve ter deletado a mensagem
        mock_consumer.delete_message.assert_called_once_with(
            "receipt-handle-123"
        )

    @pytest.mark.asyncio
    async def test_processes_one_message_at_a_time(self):
        """Worker processa 1 mensagem por chamada de _consume_one.

        Valida que cada invocação de _consume_one recebe e processa
        exatamente 1 mensagem antes de retornar (comportamento
        sequencial, MaxNumberOfMessages=1).
        """
        config = _make_config()
        worker = WorkerMain(config)

        message = _make_message()
        call_count = 0

        async def receive_one_at_a_time():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return (message, f"receipt-{call_count}")
            return None

        mock_consumer = AsyncMock()
        mock_consumer.receive_message = AsyncMock(
            side_effect=receive_one_at_a_time
        )
        mock_consumer.delete_message = AsyncMock()
        mock_consumer.extend_visibility = AsyncMock()
        worker._consumer = mock_consumer

        # Mock crawler
        mock_crawler = AsyncMock()
        mock_crawler.capture = AsyncMock(
            return_value=_make_capture_result()
        )
        mock_crawler.close = AsyncMock()
        worker._crawler = mock_crawler

        # Mock screenshot store
        mock_store = AsyncMock()
        mock_store.store = AsyncMock(
            return_value=_make_screenshot_model()
        )
        worker._screenshot_store = mock_store

        # Mock compliance analyzer
        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_compliance = AsyncMock(
            return_value=_make_compliance_report()
        )
        worker._compliance_analyzer = mock_analyzer

        # Mock event publisher
        mock_publisher = AsyncMock()
        mock_publisher.publish_compliance_completed = AsyncMock(
            return_value=True
        )
        worker._event_publisher = mock_publisher

        # Mock reference cache
        worker._reference_cache = MagicMock()
        worker._reference_cache.get_cached_images = MagicMock(
            return_value=[]
        )
        worker._reference_cache.clear = MagicMock()

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            with patch.object(
                Path, "read_bytes", return_value=b"PNG_BYTES"
            ):
                # Primeira chamada processa 1 mensagem
                await worker._consume_one()
                assert mock_consumer.receive_message.call_count == 1
                assert mock_consumer.delete_message.call_count == 1

                # Segunda chamada processa outra mensagem
                await worker._consume_one()
                assert mock_consumer.receive_message.call_count == 2
                assert mock_consumer.delete_message.call_count == 2

        # Cada chamada processou exatamente 1 mensagem
        mock_consumer.delete_message.assert_any_call("receipt-1")
        mock_consumer.delete_message.assert_any_call("receipt-2")


class TestCleanupChromium:
    """Testes para cleanup de Chromium após timeout."""

    @pytest.mark.asyncio
    async def test_closes_and_reinitializes_crawler(self):
        """Encerra crawler e cria nova instância."""
        config = _make_config()
        worker = WorkerMain(config)

        mock_crawler = AsyncMock()
        mock_crawler.close = AsyncMock()
        worker._crawler = mock_crawler

        await worker._cleanup_chromium()

        # Deve ter fechado o crawler original
        mock_crawler.close.assert_called_once()
        # Novo crawler deve ter sido criado
        assert worker._crawler is not mock_crawler
        assert worker._crawler is not None


class TestReferenceImageCacheIntegration:
    """Testes para integração do ReferenceImageCache com Worker."""

    @pytest.mark.asyncio
    async def test_cycle_change_clears_and_reloads_cache(self):
        """Mudança de ciclo limpa e recarrega o cache de referências."""
        config = _make_config()
        worker = WorkerMain(config)

        # Simular ciclo anterior
        worker._current_cycle_id = "cycle-001"

        # Mock do reference_cache
        mock_cache = MagicMock()
        mock_cache.get_cached_images = MagicMock(return_value=[])
        mock_cache.clear = MagicMock()
        mock_cache.load_and_resize = MagicMock(return_value=None)
        mock_cache.cache_image = MagicMock()
        worker._reference_cache = mock_cache

        # Mock de outros componentes
        mock_crawler = AsyncMock()
        mock_crawler.capture = AsyncMock(
            return_value=_make_capture_result()
        )
        worker._crawler = mock_crawler

        mock_store = AsyncMock()
        mock_store.store = AsyncMock(
            return_value=_make_screenshot_model()
        )
        worker._screenshot_store = mock_store

        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_compliance = AsyncMock(
            return_value=_make_compliance_report()
        )
        worker._compliance_analyzer = mock_analyzer

        mock_publisher = AsyncMock()
        mock_publisher.publish_compliance_completed = AsyncMock(
            return_value=True
        )
        worker._event_publisher = mock_publisher

        # Mensagem com ciclo DIFERENTE
        message = ProcessingMessage(
            site_id="site-001",
            cycle_id="cycle-002",  # Ciclo diferente
            brand="sky_plus",
            url="https://example.com/partner",
            rule_set_version="v1719849600_a3b2c1d4",
        )

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            with patch.object(
                Path, "read_bytes", return_value=b"PNG_BYTES"
            ):
                await worker._process_site(message)

        # Cache deve ter sido limpo devido à mudança de ciclo
        mock_cache.clear.assert_called_once()
        # Deve ter atualizado o cycle_id
        assert worker._current_cycle_id == "cycle-002"

    @pytest.mark.asyncio
    async def test_same_cycle_does_not_clear_cache(self):
        """Mesmo ciclo NÃO limpa o cache de referências."""
        config = _make_config()
        worker = WorkerMain(config)

        # Simular ciclo atual
        worker._current_cycle_id = "cycle-001"

        # Mock do reference_cache
        mock_cache = MagicMock()
        mock_cache.get_cached_images = MagicMock(return_value=[])
        mock_cache.clear = MagicMock()
        worker._reference_cache = mock_cache

        # Mock de outros componentes
        mock_crawler = AsyncMock()
        mock_crawler.capture = AsyncMock(
            return_value=_make_capture_result()
        )
        worker._crawler = mock_crawler

        mock_store = AsyncMock()
        mock_store.store = AsyncMock(
            return_value=_make_screenshot_model()
        )
        worker._screenshot_store = mock_store

        mock_analyzer = AsyncMock()
        mock_analyzer.analyze_compliance = AsyncMock(
            return_value=_make_compliance_report()
        )
        worker._compliance_analyzer = mock_analyzer

        mock_publisher = AsyncMock()
        mock_publisher.publish_compliance_completed = AsyncMock(
            return_value=True
        )
        worker._event_publisher = mock_publisher

        # Mensagem com MESMO ciclo
        message = _make_message()  # cycle_id = "cycle-001"

        with patch(
            "brand_watchdog.worker.get_session"
        ) as mock_get_session:
            mock_session = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_get_session.return_value = mock_ctx

            with patch.object(
                Path, "read_bytes", return_value=b"PNG_BYTES"
            ):
                await worker._process_site(message)

        # Cache NÃO deve ter sido limpo
        mock_cache.clear.assert_not_called()

    def test_preload_reference_images_loads_existing(self):
        """_preload_reference_images carrega imagens existentes."""
        config = _make_config()
        worker = WorkerMain(config)

        mock_cache = MagicMock()
        mock_cache.load_and_resize = MagicMock(
            return_value=b"JPEG_BYTES"
        )
        mock_cache.cache_image = MagicMock()
        worker._reference_cache = mock_cache

        with patch(
            "brand_watchdog.worker.Path.exists",
            return_value=True,
        ):
            worker._preload_reference_images()

        # Deve ter chamado load_and_resize e cache_image
        assert mock_cache.load_and_resize.call_count > 0
        assert mock_cache.cache_image.call_count > 0

    def test_preload_reference_images_skips_missing(self):
        """_preload_reference_images ignora imagens ausentes."""
        config = _make_config()
        worker = WorkerMain(config)

        mock_cache = MagicMock()
        mock_cache.load_and_resize = MagicMock(
            return_value=b"JPEG_BYTES"
        )
        mock_cache.cache_image = MagicMock()
        worker._reference_cache = mock_cache

        with patch(
            "brand_watchdog.worker.Path.exists",
            return_value=False,
        ):
            worker._preload_reference_images()

        # Não deve ter carregado nada
        mock_cache.load_and_resize.assert_not_called()
        mock_cache.cache_image.assert_not_called()


class TestWorkerShutdown:
    """Testes para shutdown graceful do Worker."""

    @pytest.mark.asyncio
    async def test_shutdown_cleans_all_resources(self):
        """Shutdown limpa crawler, cache e banco de dados."""
        config = _make_config()
        worker = WorkerMain(config)

        mock_crawler = AsyncMock()
        mock_crawler.close = AsyncMock()
        worker._crawler = mock_crawler

        mock_cache = MagicMock()
        mock_cache.clear = MagicMock()
        worker._reference_cache = mock_cache

        with patch(
            "brand_watchdog.worker.close_db",
            new_callable=AsyncMock,
        ) as mock_close_db:
            await worker.shutdown()

        mock_crawler.close.assert_called_once()
        mock_cache.clear.assert_called_once()
        mock_close_db.assert_called_once()
        assert worker._running is False

    @pytest.mark.asyncio
    async def test_shutdown_handles_crawler_error(self):
        """Shutdown continua mesmo se crawler.close() falhar."""
        config = _make_config()
        worker = WorkerMain(config)

        mock_crawler = AsyncMock()
        mock_crawler.close = AsyncMock(
            side_effect=Exception("browser crash")
        )
        worker._crawler = mock_crawler

        mock_cache = MagicMock()
        mock_cache.clear = MagicMock()
        worker._reference_cache = mock_cache

        with patch(
            "brand_watchdog.worker.close_db",
            new_callable=AsyncMock,
        ) as mock_close_db:
            # Não deve lançar exceção
            await worker.shutdown()

        # Cache e banco devem ter sido limpos mesmo assim
        mock_cache.clear.assert_called_once()
        mock_close_db.assert_called_once()
