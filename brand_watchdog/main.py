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

from brand_watchdog.config import AppConfig, load_config

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


def _create_email_provider(config: AppConfig):
    """Cria o EmailProvider adequado com base na configuração.

    Returns:
        Instância de SESProvider ou SMTPProvider.
    """
    from brand_watchdog.alerts.email_providers import (
        SESProvider,
        SMTPProvider,
    )

    if config.alert.provider == "smtp":
        return SMTPProvider(config.alert)
    return SESProvider(config.alert)


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
    from brand_watchdog.alerts.alert_service import AlertService
    from brand_watchdog.analyzer.analyzer import Analyzer
    from brand_watchdog.analyzer.bedrock_client import BedrockClient
    from brand_watchdog.coordinator.coordinator import (
        MonitoringCoordinator,
    )
    from brand_watchdog.crawler.crawler import Crawler
    from brand_watchdog.registry.brand_registry import BrandRegistry
    from brand_watchdog.registry.target_site_manager import (
        TargetSiteManager,
    )
    from brand_watchdog.scheduler.scheduler import MonitoringScheduler
    from brand_watchdog.storage.detection_store import DetectionStore
    from brand_watchdog.storage.screenshot_store import ScreenshotStore

    # Crawler
    crawler = Crawler(
        config=config.crawler,
        storage_config=config.storage,
    )

    # Analyzer + BedrockClient
    bedrock_client = BedrockClient(config=config.analyzer)
    analyzer = Analyzer(
        config=config.analyzer,
        bedrock_client=bedrock_client,
    )

    # Stores
    detection_store = DetectionStore(config=config.storage)
    screenshot_store = ScreenshotStore(config=config.storage)

    # Brand Registry
    brand_registry = BrandRegistry(
        logo_storage_path=config.storage.screenshot_base_path.parent
        / "logos",
    )

    # Target Site Manager
    target_site_manager = TargetSiteManager(
        max_target_sites=config.max_target_sites,
    )

    # Alert Service + Email Provider
    email_provider = _create_email_provider(config)
    alert_service = AlertService(
        config=config.alert,
        detection_store=detection_store,
        email_provider=email_provider,
    )

    # Monitoring Coordinator
    coordinator = MonitoringCoordinator(
        crawler=crawler,
        analyzer=analyzer,
        alert_service=alert_service,
        detection_store=detection_store,
        screenshot_store=screenshot_store,
        brand_registry=brand_registry,
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
