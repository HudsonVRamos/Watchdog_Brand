"""Script para aplicar migrações de schema via SQL direto.

Usa variável de ambiente BRAND_WATCHDOG_STORAGE_DATABASE_URL.
"""

import asyncio
import os
import sys

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


if __name__ == "__main__":
    asyncio.run(apply_schema())
