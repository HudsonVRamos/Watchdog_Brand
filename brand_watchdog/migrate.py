"""Script para aplicar migrações de schema via SQL direto.

Usa variável de ambiente BRAND_WATCHDOG_STORAGE_DATABASE_URL.
Também suporta inserção em massa de sites via --add-sites.
"""

import asyncio
import os
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def apply_schema():
    """Aplica ALTER TABLEs necessários para a evolução arquitetural."""
    db_url = os.environ.get(
        "BRAND_WATCHDOG_STORAGE_DATABASE_URL",
        os.environ.get("BRAND_WATCHDOG_DATABASE_URL", ""),
    )
    if not db_url:
        print("ERROR: DATABASE_URL não configurada")
        sys.exit(1)

    print(f"Conectando ao banco: {db_url.split('@')[1] if '@' in db_url else 'local'}")
    engine = create_async_engine(db_url)

    async with engine.begin() as conn:
        # 1. Adicionar colunas em monitoring_cycles
        await conn.execute(text(
            "ALTER TABLE monitoring_cycles "
            "ADD COLUMN IF NOT EXISTS rule_set_version VARCHAR(30)"
        ))
        print("  ✓ coluna rule_set_version adicionada")

        await conn.execute(text(
            "ALTER TABLE monitoring_cycles "
            "ADD COLUMN IF NOT EXISTS sites_dispatched INTEGER DEFAULT 0"
        ))
        print("  ✓ coluna sites_dispatched adicionada")

        # 2. Renomear file_path → s3_key (se ainda não renomeado)
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'screenshots' AND column_name = 'file_path'"
        ))
        if result.fetchone():
            await conn.execute(text(
                "ALTER TABLE screenshots RENAME COLUMN file_path TO s3_key"
            ))
            print("  ✓ coluna file_path renomeada para s3_key")
        else:
            print("  - coluna s3_key já existe (skip)")

    await engine.dispose()
    print("Schema atualizado com sucesso!")


async def add_sites_bulk():
    """Adiciona sites em massa a partir da variável BRAND_WATCHDOG_BULK_SITES ou stdin."""
    db_url = os.environ.get(
        "BRAND_WATCHDOG_STORAGE_DATABASE_URL",
        os.environ.get("BRAND_WATCHDOG_DATABASE_URL", ""),
    )
    if not db_url:
        print("ERROR: DATABASE_URL não configurada")
        sys.exit(1)

    # Sites podem vir de env var ou de argumento
    sites_str = os.environ.get("BRAND_WATCHDOG_BULK_SITES", "")
    brand = os.environ.get("BRAND_WATCHDOG_BULK_BRAND", "sky_plus")

    if not sites_str:
        print("ERROR: BRAND_WATCHDOG_BULK_SITES não definida")
        sys.exit(1)

    urls = [u.strip() for u in sites_str.split(",") if u.strip()]
    print(f"Adicionando {len(urls)} sites (brand={brand})...")

    engine = create_async_engine(db_url)

    added = 0
    skipped = 0

    async with engine.begin() as conn:
        # Get existing normalized URLs
        result = await conn.execute(text(
            "SELECT normalized_url FROM target_sites"
        ))
        existing = {row[0] for row in result.fetchall()}

        for url in urls:
            norm = url.lower().rstrip("/").split("#")[0]
            if norm in existing:
                skipped += 1
                continue

            site_id = str(uuid.uuid4())
            await conn.execute(
                text(
                    "INSERT INTO target_sites (id, url, normalized_url, brand, active) "
                    "VALUES (:id, :url, :norm, :brand, true)"
                ),
                {"id": site_id, "url": url, "norm": norm, "brand": brand},
            )
            existing.add(norm)
            added += 1

    await engine.dispose()
    print(f"  ✓ Sites adicionados: {added}")
    print(f"  - Sites já existentes (skip): {skipped}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--add-sites":
        asyncio.run(add_sites_bulk())
    else:
        asyncio.run(apply_schema())


if __name__ == "__main__":
    asyncio.run(apply_schema())
