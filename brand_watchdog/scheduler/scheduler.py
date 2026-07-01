"""Agendador de ciclos de monitoramento usando APScheduler.

Wrapper sobre APScheduler com intervalo configurável (1-720 horas).
Dispara MonitoringCoordinator.run_cycle() no intervalo definido.
Inclui job de cleanup para remoção de dados expirados.

Requirements: 5.1, 5.2, 7.3, 7.4, 8.3, 8.4
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from brand_watchdog.config import ScheduleConfig

if TYPE_CHECKING:
    from brand_watchdog.coordinator.coordinator import (
        MonitoringCoordinator,
    )
    from brand_watchdog.storage.detection_store import (
        DetectionStore,
    )
    from brand_watchdog.storage.screenshot_store import (
        ScreenshotStore,
    )

logger = logging.getLogger(__name__)

_MIN_INTERVAL_HOURS = 1
_MAX_INTERVAL_HOURS = 720
_JOB_ID = "monitoring_cycle"
_CLEANUP_JOB_ID = "cleanup_expired"
_CLEANUP_INTERVAL_HOURS = 24


class MonitoringScheduler:
    """Agendador de ciclos de monitoramento de marca.

    Encapsula APScheduler para disparar ciclos de monitoramento
    em intervalos configuráveis. Suporta atualização dinâmica do
    intervalo sem reiniciar o scheduler.

    Args:
        coordinator: Instância do MonitoringCoordinator que
            executa os ciclos de monitoramento.
        config: Configuração de agendamento com interval_hours.
    """

    def __init__(
        self,
        coordinator: MonitoringCoordinator,
        config: ScheduleConfig,
    ) -> None:
        self._coordinator = coordinator
        self._config = config
        self._scheduler = AsyncIOScheduler()
        self._running = False

    @property
    def is_running(self) -> bool:
        """Indica se o scheduler está em execução."""
        return self._running

    @property
    def interval_hours(self) -> int:
        """Retorna o intervalo atual em horas."""
        return self._config.interval_hours

    def start(self) -> None:
        """Inicia o scheduler com o intervalo configurado.

        Adiciona o job de monitoramento e inicia o loop do
        APScheduler. Se já estiver em execução, não faz nada.

        Raises:
            RuntimeError: Se o scheduler já estiver em execução.
        """
        if self._running:
            logger.warning(
                "Scheduler já está em execução, "
                "ignorando chamada start()"
            )
            return

        self._scheduler.add_job(
            func=self._trigger_cycle,
            trigger=IntervalTrigger(
                hours=self._config.interval_hours,
            ),
            id=_JOB_ID,
            name="Ciclo de monitoramento de marca",
            replace_existing=True,
        )

        self._scheduler.start()
        self._running = True

        logger.info(
            "Scheduler iniciado com intervalo de %d hora(s)",
            self._config.interval_hours,
        )

    def stop(self) -> None:
        """Para o scheduler gracefully.

        Encerra o APScheduler e aguarda jobs em execução.
        Se não estiver em execução, não faz nada.
        """
        if not self._running:
            logger.warning(
                "Scheduler não está em execução, "
                "ignorando chamada stop()"
            )
            return

        self._scheduler.shutdown(wait=True)
        self._running = False

        logger.info("Scheduler parado com sucesso")

    def update_interval(self, hours: int) -> None:
        """Atualiza o intervalo de execução do scheduler.

        Valida que o valor está entre 1 e 720 horas e
        reagenda o job com o novo intervalo.

        Args:
            hours: Novo intervalo em horas (1-720).

        Raises:
            ValueError: Se hours estiver fora do range 1-720.
        """
        if not (_MIN_INTERVAL_HOURS <= hours <= _MAX_INTERVAL_HOURS):
            raise ValueError(
                f"interval_hours deve estar entre "
                f"{_MIN_INTERVAL_HOURS} e {_MAX_INTERVAL_HOURS}, "
                f"valor recebido: {hours}"
            )

        old_interval = self._config.interval_hours
        self._config.interval_hours = hours

        if self._running:
            self._scheduler.reschedule_job(
                job_id=_JOB_ID,
                trigger=IntervalTrigger(hours=hours),
            )

        logger.info(
            "Intervalo do scheduler atualizado: "
            "%d hora(s) → %d hora(s)",
            old_interval,
            hours,
        )

    def register_cleanup_job(
        self,
        detection_store: DetectionStore,
        screenshot_store: ScreenshotStore,
    ) -> None:
        """Registra job de cleanup de dados expirados.

        O job roda diariamente (a cada 24 horas) e remove:
        - Detecções expiradas (em batches de 100)
        - Screenshots expirados e seus arquivos físicos
          (em batches de 100)

        Deve ser chamado antes de start() para que o job
        seja registrado junto com o scheduler.

        Args:
            detection_store: Store de detecções para cleanup.
            screenshot_store: Store de screenshots para cleanup.
        """
        self._detection_store = detection_store
        self._screenshot_store = screenshot_store

        self._scheduler.add_job(
            func=self._run_cleanup,
            trigger=IntervalTrigger(
                hours=_CLEANUP_INTERVAL_HOURS,
            ),
            id=_CLEANUP_JOB_ID,
            name="Cleanup de dados expirados",
            replace_existing=True,
        )

        logger.info(
            "Job de cleanup registrado: "
            "intervalo=%d hora(s)",
            _CLEANUP_INTERVAL_HOURS,
        )

    async def _run_cleanup(self) -> None:
        """Executa cleanup de detecções e screenshots expirados.

        Chama cleanup_expired() em ambos os stores e loga
        os resultados. Captura exceções para não derrubar
        o scheduler.
        """
        logger.info("Iniciando cleanup de dados expirados")

        try:
            detections_removed = (
                await self._detection_store.cleanup_expired()
            )
            screenshots_removed = (
                await self._screenshot_store.cleanup_expired()
            )

            logger.info(
                "Cleanup concluído: "
                "%d detecções removidas, "
                "%d screenshots removidos",
                detections_removed,
                screenshots_removed,
            )
        except Exception as exc:
            logger.error(
                "Erro durante cleanup de dados expirados: %s",
                str(exc),
                exc_info=True,
            )

    async def _trigger_cycle(self) -> None:
        """Dispara um ciclo de monitoramento via coordinator.

        Método interno chamado pelo APScheduler no intervalo
        configurado. Captura exceções para não derrubar o
        scheduler em caso de erro no ciclo.
        """
        logger.info("Scheduler disparando ciclo de monitoramento")

        try:
            await self._coordinator.run_cycle()
        except Exception as exc:
            logger.error(
                "Erro durante ciclo de monitoramento "
                "disparado pelo scheduler: %s",
                str(exc),
                exc_info=True,
            )
