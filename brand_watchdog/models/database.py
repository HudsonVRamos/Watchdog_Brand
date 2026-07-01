"""Configuração e inicialização do banco de dados async com SQLAlchemy.

Provê engine, session factory e função de inicialização de tabelas.
Usa sqlalchemy.ext.asyncio para operações assíncronas com aiosqlite.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.entities import Base

# Engine e session factory globais (inicializados via setup_database)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def setup_database(config: StorageConfig | None = None) -> None:
    """Configura engine e session factory a partir de StorageConfig.

    Deve ser chamado na inicialização da aplicação antes de usar
    get_session() ou init_db().

    Args:
        config: Configuração de storage. Se None, usa defaults.
    """
    global _engine, _session_factory

    if config is None:
        config = StorageConfig()

    _engine = create_async_engine(
        config.database_url,
        echo=False,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def get_engine() -> AsyncEngine:
    """Retorna o engine async configurado.

    Raises:
        RuntimeError: Se setup_database() não foi chamado.
    """
    if _engine is None:
        raise RuntimeError(
            "Database não configurado. "
            "Chame setup_database() antes de usar get_engine()."
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Retorna a session factory configurada.

    Raises:
        RuntimeError: Se setup_database() não foi chamado.
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database não configurado. "
            "Chame setup_database() antes de usar get_session_factory()."
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager assíncrono que fornece uma sessão do banco.

    Uso:
        async with get_session() as session:
            result = await session.execute(...)

    Raises:
        RuntimeError: Se setup_database() não foi chamado.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Cria todas as tabelas definidas nos modelos.

    Usa o engine configurado para executar
    Base.metadata.create_all de forma síncrona dentro
    de uma conexão async.

    Raises:
        RuntimeError: Se setup_database() não foi chamado.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Fecha o engine e libera recursos de conexão.

    Seguro para chamar mesmo se o engine não estiver configurado.
    """
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
