"""Ponto de entrada principal do Brand Watchdog.

Inicializa todos os componentes com injeção de dependências,
configura banco de dados, scheduler e signal handling para
graceful shutdown.

Requirements: 5.1
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from brand_watchdog.config import load_config

logger = logging.getLogger(__name__)

# Caminho padrão para o arquivo de configuração YAML
_DEFAULT_CONFIG_PATH = Path("config.yaml")


def _setup_logging() -> None:
    """Configura logging padrão da aplicação."""
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s [%(levelname)s] "
            "%(name)s: %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _resolve_config_path() -> Path | None:
    """Resolve o caminho do arquivo de configuração YAML.

    Retorna o caminho se o arquivo existir, None caso contrário
    (permitindo uso apenas de defaults + env vars).
    """
    config_path = _DEFAULT_CONFIG_PATH
    if config_path.exists():
        return config_path
    logger.info(
        "Arquivo de configuração '%s' não encontrado. "
        "Usando valores padrão com overrides de variáveis "
        "de ambiente.",
        config_path,
    )
    return None


async def main() -> None:
    """Inicializa e executa o Brand Watchdog.

    Fluxo:
        1. Carrega configuração (YAML + env vars)
        2. Configura e inicializa banco de dados
        3. Instancia todos os componentes com DI
        4. Inicia o scheduler
        5. Aguarda sinal de shutdown (SIGTERM/SIGINT)
        6. Executa graceful shutdown
    """
    # 1. Carregar configuração
    config_path = _resolve_config_path()
    config = load_config(yaml_path=config_path)
    logger.info("Configuração carregada com sucesso")

    # 2. Setup do banco de dados
    from brand_watchdog.models.database import (
        close_db,
        init_db,
        setup_database,
    )

    setup_database(config.storage)
    await init_db()
    logger.info("Banco de dados inicializado")

    # 3. Instanciar componentes com injeção de dependências
    from brand_watchdog.coordinator.coordinator import (
        MonitoringCoordinator,
    )
    from brand_watchdog.coordinator.cycle_consolidator import (
        CycleConsolidator,
    )
    from brand_watchdog.queue.publisher import SQSPublisher
    from brand_watchdog.registry.target_site_manager import (
        TargetSiteManager,
    )
    from brand_watchdog.scheduler.scheduler import MonitoringScheduler
    from brand_watchdog.utils.rule_set_version import (
        RuleSetVersionCalculator,
    )

    # Target Site Manager
    target_site_manager = TargetSiteManager(
        max_target_sites=config.max_target_sites,
    )

    # Monitoring Coordinator (distribuído)
    rules_dir = Path("watchdog_rules")
    rule_set_calculator = RuleSetVersionCalculator(
        rules_dir=rules_dir,
    )
    sqs_publisher = SQSPublisher(
        queue_url=config.queue.queue_url,
        region=config.storage.s3_region,
    )
    consolidator = CycleConsolidator(config=config.worker)

    coordinator = MonitoringCoordinator(
        rule_set_calculator=rule_set_calculator,
        sqs_publisher=sqs_publisher,
        consolidator=consolidator,
        target_site_manager=target_site_manager,
        config=config,
    )

    # Monitoring Scheduler
    scheduler = MonitoringScheduler(
        coordinator=coordinator,
        config=config.schedule,
    )

    # 4. Iniciar scheduler
    scheduler.start()
    logger.info(
        "Brand Watchdog em execução — intervalo de "
        "monitoramento: %d hora(s)",
        config.schedule.interval_hours,
    )

    # 5. Aguardar sinal de shutdown com graceful handling
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: signal.Signals, *_) -> None:
        """Handler para SIGTERM e SIGINT."""
        logger.info(
            "Sinal %s recebido. Iniciando shutdown...",
            sig.name,
        )
        shutdown_event.set()

    # Registrar signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig, _signal_handler, sig
            )
        except NotImplementedError:
            # Windows não suporta add_signal_handler
            signal.signal(sig, _signal_handler)

    # Aguardar shutdown
    await shutdown_event.wait()

    # 6. Graceful shutdown
    logger.info("Executando graceful shutdown...")
    scheduler.stop()
    await close_db()
    logger.info("Brand Watchdog encerrado com sucesso")


if __name__ == "__main__":
    _setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Brand Watchdog interrompido pelo usuário")
