"""CLI de administração do Brand Watchdog.

Permite gerenciar target sites, brand assets e disparar ciclos
de monitoramento manualmente.

Uso:
    python -m brand_watchdog.cli add-site https://www.skymais.com.br/home
    python -m brand_watchdog.cli add-site https://dgo.com/promo --brand dgo
    python -m brand_watchdog.cli add-text "SKY"
    python -m brand_watchdog.cli list-sites
    python -m brand_watchdog.cli list-assets
    python -m brand_watchdog.cli run-cycle
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _setup():
    """Inicializa banco e retorna componentes necessários."""
    from brand_watchdog.config import load_config
    from brand_watchdog.models.database import (
        init_db,
        setup_database,
    )

    config_path = Path("config.yaml")
    config = load_config(config_path if config_path.exists() else None)
    setup_database(config.storage)
    await init_db()
    return config


async def add_site(url: str, brand: str = "sky_plus") -> None:
    """Adiciona um target site para monitoramento."""
    config = await _setup()

    from brand_watchdog.config import BRAND_TYPES
    from brand_watchdog.registry.target_site_manager import (
        TargetSiteManager,
    )

    if brand not in BRAND_TYPES:
        print(
            f"❌ Brand inválido: '{brand}'. "
            f"Valores aceitos: {BRAND_TYPES}"
        )
        sys.exit(1)

    manager = TargetSiteManager(
        max_target_sites=config.max_target_sites,
    )

    try:
        site = await manager.register(url, brand=brand)
        print(f"✅ Site registrado com sucesso!")
        print(f"   ID: {site.id}")
        print(f"   URL: {site.url}")
        print(f"   Normalizada: {site.normalized_url}")
        print(f"   Brand: {site.brand}")
    except Exception as e:
        print(f"❌ Erro ao registrar site: {e}")
        sys.exit(1)


async def remove_site(site_id: str) -> None:
    """Remove um target site."""
    config = await _setup()

    from brand_watchdog.registry.target_site_manager import (
        TargetSiteManager,
    )

    manager = TargetSiteManager(
        max_target_sites=config.max_target_sites,
    )

    success = await manager.remove(site_id)
    if success:
        print(f"✅ Site {site_id} removido.")
    else:
        print(f"❌ Site {site_id} não encontrado.")


async def list_sites() -> None:
    """Lista todos os target sites."""
    config = await _setup()

    from brand_watchdog.registry.target_site_manager import (
        TargetSiteManager,
    )

    manager = TargetSiteManager(
        max_target_sites=config.max_target_sites,
    )

    sites = await manager.list_all()
    if not sites:
        print("Nenhum site registrado.")
        return

    print(f"{'='*60}")
    print(f"  Target Sites ({len(sites)} registrados)")
    print(f"{'='*60}")
    for site in sites:
        status = "🟢 Ativo" if site.active else "🔴 Inativo"
        brand_label = "SKY+" if site.brand == "sky_plus" else "DGO"
        print(f"  {status} [{brand_label}] {site.url}")
        print(f"       ID: {site.id}")
        print(f"       Criado: {site.created_at}")
        print()


async def add_text(text: str) -> None:
    """Adiciona um brand asset do tipo texto."""
    config = await _setup()

    from brand_watchdog.registry.brand_registry import BrandRegistry

    registry = BrandRegistry(
        logo_storage_path=config.storage.screenshot_base_path.parent
        / "logos",
    )

    try:
        asset = await registry.register_text(text)
        print(f"✅ Texto de marca registrado!")
        print(f"   ID: {asset.id}")
        print(f"   Texto: {asset.text_value}")
        print(f"   Hash: {asset.content_hash}")
    except Exception as e:
        print(f"❌ Erro ao registrar texto: {e}")
        sys.exit(1)


async def add_logo(file_path: str) -> None:
    """Adiciona um brand asset do tipo logo (imagem)."""
    config = await _setup()

    from brand_watchdog.registry.brand_registry import BrandRegistry

    registry = BrandRegistry(
        logo_storage_path=config.storage.screenshot_base_path.parent
        / "logos",
    )

    path = Path(file_path)
    if not path.exists():
        print(f"❌ Arquivo não encontrado: {file_path}")
        sys.exit(1)

    try:
        image_data = path.read_bytes()
        asset = await registry.register_logo(image_data, path.name)
        print(f"✅ Logo registrado!")
        print(f"   ID: {asset.id}")
        print(f"   Arquivo: {asset.original_filename}")
        print(f"   Tamanho: {asset.file_size_bytes} bytes")
        print(f"   Hash: {asset.content_hash}")
    except Exception as e:
        print(f"❌ Erro ao registrar logo: {e}")
        sys.exit(1)


async def list_assets() -> None:
    """Lista todos os brand assets."""
    config = await _setup()

    from brand_watchdog.registry.brand_registry import BrandRegistry

    registry = BrandRegistry(
        logo_storage_path=config.storage.screenshot_base_path.parent
        / "logos",
    )

    assets = await registry.get_all_assets()
    if not assets:
        print("Nenhum brand asset registrado.")
        return

    print(f"{'='*60}")
    print(f"  Brand Assets ({len(assets)} registrados)")
    print(f"{'='*60}")
    for asset in assets:
        tipo = "🖼️  Logo" if asset.asset_type == "logo" else "📝 Texto"
        valor = asset.original_filename or asset.text_value
        print(f"  {tipo}: {valor}")
        print(f"       ID: {asset.id}")
        print(f"       Hash: {asset.content_hash}")
        print()


async def run_cycle() -> None:
    """Dispara um ciclo de monitoramento manualmente."""
    config = await _setup()

    from brand_watchdog.alerts.compliance_email_notifier import (
        ComplianceEmailNotifier,
    )
    from brand_watchdog.alerts.email_providers import (
        create_email_provider,
    )
    from brand_watchdog.analyzer.bedrock_client import BedrockClient
    from brand_watchdog.analyzer.compliance_analyzer import (
        ComplianceAnalyzer,
    )
    from brand_watchdog.coordinator.coordinator import (
        MonitoringCoordinator,
    )
    from brand_watchdog.crawler.crawler import Crawler
    from brand_watchdog.registry.target_site_manager import (
        TargetSiteManager,
    )
    from brand_watchdog.storage.detection_store import DetectionStore
    from brand_watchdog.storage.screenshot_store import ScreenshotStore

    crawler = Crawler(config=config.crawler, storage_config=config.storage)
    bedrock_client = BedrockClient(config=config.analyzer)
    detection_store = DetectionStore(config=config.storage)
    compliance_analyzer = ComplianceAnalyzer(
        config=config.analyzer,
        bedrock_client=bedrock_client,
        detection_store=detection_store,
        storage_config=config.storage,
    )
    screenshot_store = ScreenshotStore(config=config.storage)
    target_site_manager = TargetSiteManager(
        max_target_sites=config.max_target_sites
    )
    email_provider = create_email_provider(config.alert)
    compliance_notifier = ComplianceEmailNotifier(
        config=config.alert,
        email_provider=email_provider,
    )

    coordinator = MonitoringCoordinator(
        crawler=crawler,
        compliance_analyzer=compliance_analyzer,
        compliance_notifier=compliance_notifier,
        detection_store=detection_store,
        screenshot_store=screenshot_store,
        target_site_manager=target_site_manager,
        config=config,
    )

    print("🔄 Iniciando ciclo de monitoramento...")
    result = await coordinator.run_cycle()

    print(f"\n{'='*60}")
    print(f"  Ciclo Concluído")
    print(f"{'='*60}")
    print(f"  Sites processados: {result.sites_processed}")
    print(f"  Sites com falha:   {result.sites_failed}")
    print(f"  Detecções:         {result.detections_found}")
    print(f"  Início:            {result.started_at}")
    print(f"  Fim:               {result.ended_at}")
    print()

    for sr in result.site_results:
        status = "✅" if sr.success else "❌"
        print(f"  {status} {sr.target_url}")
        if not sr.success:
            print(f"     Erro: {sr.error_message}")
        if sr.detections:
            for d in sr.detections:
                print(
                    f"     🔍 {d.match_type} "
                    f"(conf: {d.confidence}%) - {d.description[:60]}"
                )


def main():
    """Entry point da CLI."""
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python -m brand_watchdog.cli add-site <url> [--brand sky_plus|dgo]")
        print("  python -m brand_watchdog.cli remove-site <id>")
        print("  python -m brand_watchdog.cli list-sites")
        print("  python -m brand_watchdog.cli add-text <texto>")
        print("  python -m brand_watchdog.cli add-logo <caminho>")
        print("  python -m brand_watchdog.cli list-assets")
        print("  python -m brand_watchdog.cli run-cycle")
        sys.exit(1)

    command = sys.argv[1]

    if command == "add-site":
        if len(sys.argv) < 3:
            print("❌ Forneça a URL: add-site <url> [--brand sky_plus|dgo]")
            sys.exit(1)
        url = sys.argv[2]
        brand = "sky_plus"
        # Parse --brand flag
        remaining_args = sys.argv[3:]
        if "--brand" in remaining_args:
            idx = remaining_args.index("--brand")
            if idx + 1 < len(remaining_args):
                brand = remaining_args[idx + 1]
            else:
                print("❌ Forneça o valor do brand: --brand sky_plus|dgo")
                sys.exit(1)
        asyncio.run(add_site(url, brand=brand))

    elif command == "remove-site":
        if len(sys.argv) < 3:
            print("❌ Forneça o ID: remove-site <id>")
            sys.exit(1)
        asyncio.run(remove_site(sys.argv[2]))

    elif command == "list-sites":
        asyncio.run(list_sites())

    elif command == "add-text":
        if len(sys.argv) < 3:
            print("❌ Forneça o texto: add-text <texto>")
            sys.exit(1)
        asyncio.run(add_text(sys.argv[2]))

    elif command == "add-logo":
        if len(sys.argv) < 3:
            print("❌ Forneça o caminho: add-logo <arquivo>")
            sys.exit(1)
        asyncio.run(add_logo(sys.argv[2]))

    elif command == "list-assets":
        asyncio.run(list_assets())

    elif command == "run-cycle":
        asyncio.run(run_cycle())

    else:
        print(f"❌ Comando desconhecido: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
