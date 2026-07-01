"""Testes unitários para o MonitoringScheduler.

Testa start, stop, update_interval, disparo de ciclo e cleanup job.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.config import ScheduleConfig
from brand_watchdog.scheduler.scheduler import (
    MonitoringScheduler,
    _CLEANUP_INTERVAL_HOURS,
    _CLEANUP_JOB_ID,
    _JOB_ID,
    _MAX_INTERVAL_HOURS,
    _MIN_INTERVAL_HOURS,
)


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Coordinator mock com run_cycle assíncrono."""
    coordinator = MagicMock()
    coordinator.run_cycle = AsyncMock()
    return coordinator


@pytest.fixture
def schedule_config() -> ScheduleConfig:
    """Configuração de schedule padrão."""
    return ScheduleConfig(interval_hours=24)


@pytest.fixture
def scheduler(
    mock_coordinator: MagicMock,
    schedule_config: ScheduleConfig,
) -> MonitoringScheduler:
    """Instância do scheduler para testes."""
    return MonitoringScheduler(
        coordinator=mock_coordinator,
        config=schedule_config,
    )


class TestMonitoringSchedulerInit:
    """Testes de inicialização do scheduler."""

    def test_cria_scheduler_com_estado_parado(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """Scheduler começa parado após instanciação."""
        assert scheduler.is_running is False

    def test_interval_hours_reflete_config(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """Propriedade interval_hours reflete a config."""
        assert scheduler.interval_hours == 24


class TestSchedulerStart:
    """Testes do método start()."""

    def test_start_inicia_scheduler(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """start() coloca scheduler em estado running."""
        with patch.object(
            scheduler._scheduler, "add_job"
        ) as mock_add, patch.object(
            scheduler._scheduler, "start"
        ) as mock_start:
            scheduler.start()

            assert scheduler.is_running is True
            mock_add.assert_called_once()
            mock_start.assert_called_once()

    def test_start_configura_job_com_intervalo_correto(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """start() adiciona job com intervalo da config."""
        with patch.object(
            scheduler._scheduler, "add_job"
        ) as mock_add, patch.object(
            scheduler._scheduler, "start"
        ):
            scheduler.start()

            call_kwargs = mock_add.call_args
            trigger = call_kwargs.kwargs["trigger"]
            assert trigger.interval.total_seconds() == 24 * 3600
            assert call_kwargs.kwargs["id"] == _JOB_ID

    def test_start_duplo_nao_inicia_novamente(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """Chamar start() duas vezes não reinicia o scheduler."""
        with patch.object(
            scheduler._scheduler, "add_job"
        ), patch.object(
            scheduler._scheduler, "start"
        ) as mock_start:
            scheduler.start()
            scheduler.start()  # Segunda chamada ignorada

            mock_start.assert_called_once()


class TestSchedulerStop:
    """Testes do método stop()."""

    def test_stop_para_scheduler(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """stop() encerra scheduler em execução."""
        with patch.object(
            scheduler._scheduler, "add_job"
        ), patch.object(
            scheduler._scheduler, "start"
        ), patch.object(
            scheduler._scheduler, "shutdown"
        ) as mock_shutdown:
            scheduler.start()
            scheduler.stop()

            assert scheduler.is_running is False
            mock_shutdown.assert_called_once_with(wait=True)

    def test_stop_sem_start_nao_faz_nada(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """stop() sem start() é um no-op seguro."""
        with patch.object(
            scheduler._scheduler, "shutdown"
        ) as mock_shutdown:
            scheduler.stop()

            mock_shutdown.assert_not_called()
            assert scheduler.is_running is False


class TestUpdateInterval:
    """Testes do método update_interval()."""

    def test_update_interval_valido(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """update_interval com valor válido atualiza config."""
        with patch.object(
            scheduler._scheduler, "add_job"
        ), patch.object(
            scheduler._scheduler, "start"
        ), patch.object(
            scheduler._scheduler, "reschedule_job"
        ) as mock_reschedule:
            scheduler.start()
            scheduler.update_interval(48)

            assert scheduler.interval_hours == 48
            mock_reschedule.assert_called_once()
            call_kwargs = mock_reschedule.call_args
            assert call_kwargs.kwargs["job_id"] == _JOB_ID

    def test_update_interval_minimo(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """update_interval aceita valor mínimo de 1 hora."""
        scheduler.update_interval(_MIN_INTERVAL_HOURS)
        assert scheduler.interval_hours == 1

    def test_update_interval_maximo(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """update_interval aceita valor máximo de 720 horas."""
        scheduler.update_interval(_MAX_INTERVAL_HOURS)
        assert scheduler.interval_hours == 720

    def test_update_interval_abaixo_minimo_raises(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """update_interval rejeita valor abaixo do mínimo."""
        with pytest.raises(ValueError, match="1 e 720"):
            scheduler.update_interval(0)

    def test_update_interval_acima_maximo_raises(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """update_interval rejeita valor acima do máximo."""
        with pytest.raises(ValueError, match="1 e 720"):
            scheduler.update_interval(721)

    def test_update_interval_negativo_raises(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """update_interval rejeita valor negativo."""
        with pytest.raises(ValueError):
            scheduler.update_interval(-5)

    def test_update_interval_sem_scheduler_ativo(
        self, scheduler: MonitoringScheduler
    ) -> None:
        """update_interval funciona mesmo sem scheduler ativo."""
        scheduler.update_interval(12)
        assert scheduler.interval_hours == 12


class TestTriggerCycle:
    """Testes do disparo do ciclo de monitoramento."""

    @pytest.mark.asyncio
    async def test_trigger_cycle_chama_coordinator(
        self,
        scheduler: MonitoringScheduler,
        mock_coordinator: MagicMock,
    ) -> None:
        """_trigger_cycle() chama coordinator.run_cycle()."""
        await scheduler._trigger_cycle()

        mock_coordinator.run_cycle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_cycle_captura_excecao(
        self,
        scheduler: MonitoringScheduler,
        mock_coordinator: MagicMock,
    ) -> None:
        """_trigger_cycle() não propaga exceções do coordinator."""
        mock_coordinator.run_cycle.side_effect = RuntimeError(
            "Erro simulado"
        )

        # Não deve levantar exceção
        await scheduler._trigger_cycle()

        mock_coordinator.run_cycle.assert_awaited_once()


@pytest.fixture
def mock_detection_store() -> MagicMock:
    """DetectionStore mock com cleanup_expired assíncrono."""
    store = MagicMock()
    store.cleanup_expired = AsyncMock(return_value=5)
    return store


@pytest.fixture
def mock_screenshot_store() -> MagicMock:
    """ScreenshotStore mock com cleanup_expired assíncrono."""
    store = MagicMock()
    store.cleanup_expired = AsyncMock(return_value=3)
    return store


class TestRegisterCleanupJob:
    """Testes do método register_cleanup_job()."""

    def test_registra_job_de_cleanup_no_scheduler(
        self,
        scheduler: MonitoringScheduler,
        mock_detection_store: MagicMock,
        mock_screenshot_store: MagicMock,
    ) -> None:
        """register_cleanup_job() adiciona job ao APScheduler."""
        with patch.object(
            scheduler._scheduler, "add_job"
        ) as mock_add:
            scheduler.register_cleanup_job(
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
            )

            mock_add.assert_called_once()
            call_kwargs = mock_add.call_args
            assert call_kwargs.kwargs["id"] == _CLEANUP_JOB_ID
            assert (
                call_kwargs.kwargs["name"]
                == "Cleanup de dados expirados"
            )

    def test_cleanup_job_intervalo_24_horas(
        self,
        scheduler: MonitoringScheduler,
        mock_detection_store: MagicMock,
        mock_screenshot_store: MagicMock,
    ) -> None:
        """Cleanup job é registrado com intervalo de 24 horas."""
        with patch.object(
            scheduler._scheduler, "add_job"
        ) as mock_add:
            scheduler.register_cleanup_job(
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
            )

            call_kwargs = mock_add.call_args
            trigger = call_kwargs.kwargs["trigger"]
            expected_seconds = _CLEANUP_INTERVAL_HOURS * 3600
            assert (
                trigger.interval.total_seconds()
                == expected_seconds
            )

    def test_armazena_stores_como_atributos(
        self,
        scheduler: MonitoringScheduler,
        mock_detection_store: MagicMock,
        mock_screenshot_store: MagicMock,
    ) -> None:
        """register_cleanup_job() guarda referências aos stores."""
        with patch.object(scheduler._scheduler, "add_job"):
            scheduler.register_cleanup_job(
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
            )

        assert scheduler._detection_store is mock_detection_store
        assert (
            scheduler._screenshot_store is mock_screenshot_store
        )


class TestRunCleanup:
    """Testes do método _run_cleanup()."""

    @pytest.mark.asyncio
    async def test_run_cleanup_chama_ambos_stores(
        self,
        scheduler: MonitoringScheduler,
        mock_detection_store: MagicMock,
        mock_screenshot_store: MagicMock,
    ) -> None:
        """_run_cleanup() chama cleanup_expired em ambos stores."""
        with patch.object(scheduler._scheduler, "add_job"):
            scheduler.register_cleanup_job(
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
            )

        await scheduler._run_cleanup()

        mock_detection_store.cleanup_expired.assert_awaited_once()
        mock_screenshot_store.cleanup_expired.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_cleanup_nao_propaga_excecao(
        self,
        scheduler: MonitoringScheduler,
        mock_detection_store: MagicMock,
        mock_screenshot_store: MagicMock,
    ) -> None:
        """_run_cleanup() captura exceções sem propagar."""
        mock_detection_store.cleanup_expired.side_effect = (
            RuntimeError("Erro simulado no banco")
        )

        with patch.object(scheduler._scheduler, "add_job"):
            scheduler.register_cleanup_job(
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
            )

        # Não deve levantar exceção
        await scheduler._run_cleanup()

    @pytest.mark.asyncio
    async def test_run_cleanup_loga_resultados(
        self,
        scheduler: MonitoringScheduler,
        mock_detection_store: MagicMock,
        mock_screenshot_store: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_run_cleanup() loga quantidade de itens removidos."""
        import logging

        with patch.object(scheduler._scheduler, "add_job"):
            scheduler.register_cleanup_job(
                detection_store=mock_detection_store,
                screenshot_store=mock_screenshot_store,
            )

        with caplog.at_level(logging.INFO):
            await scheduler._run_cleanup()

        assert "5 detecções removidas" in caplog.text
        assert "3 screenshots removidos" in caplog.text
