"""Testes unitários para o módulo database.py.

Valida configuração de engine, session factory e init_db.
"""

import pytest

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    get_engine,
    get_session,
    get_session_factory,
    init_db,
    setup_database,
)


@pytest.fixture(autouse=True)
async def cleanup_db():
    """Garante que o banco é fechado após cada teste."""
    yield
    await close_db()


class TestSetupDatabase:
    """Testes para setup_database()."""

    def test_setup_com_config_default(self):
        """Deve configurar engine com URL padrão quando config é None."""
        setup_database()
        engine = get_engine()
        assert engine is not None
        assert "brand_watchdog" in str(engine.url)

    def test_setup_com_config_customizada(self):
        """Deve usar database_url do StorageConfig fornecido."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///./test_custom.db"
        )
        setup_database(config)
        engine = get_engine()
        assert "test_custom" in str(engine.url)

    def test_setup_com_sqlite_memory(self):
        """Deve funcionar com banco in-memory."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:"
        )
        setup_database(config)
        engine = get_engine()
        assert "memory" in str(engine.url)


class TestGetEngine:
    """Testes para get_engine()."""

    def test_get_engine_sem_setup_levanta_runtime_error(self):
        """Deve levantar RuntimeError se setup_database não foi chamado."""
        with pytest.raises(RuntimeError, match="Database não configurado"):
            get_engine()

    def test_get_engine_apos_setup(self):
        """Deve retornar engine válido após setup."""
        setup_database()
        engine = get_engine()
        assert engine is not None


class TestGetSessionFactory:
    """Testes para get_session_factory()."""

    def test_factory_sem_setup_levanta_runtime_error(self):
        """Deve levantar RuntimeError se setup_database não foi chamado."""
        with pytest.raises(RuntimeError, match="Database não configurado"):
            get_session_factory()

    def test_factory_apos_setup(self):
        """Deve retornar session factory válida após setup."""
        setup_database()
        factory = get_session_factory()
        assert factory is not None


class TestGetSession:
    """Testes para get_session() context manager."""

    async def test_session_sem_setup_levanta_runtime_error(self):
        """Deve levantar RuntimeError se setup não foi feito."""
        with pytest.raises(RuntimeError, match="Database não configurado"):
            async with get_session():
                pass

    async def test_session_retorna_async_session(self):
        """Deve retornar uma AsyncSession funcional."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:"
        )
        setup_database(config)
        await init_db()

        async with get_session() as session:
            assert session is not None
            # Verifica que é possível executar queries
            result = await session.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
            assert result.scalar() == 1


class TestInitDb:
    """Testes para init_db()."""

    async def test_init_db_cria_tabelas(self):
        """Deve criar todas as tabelas dos modelos."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:"
        )
        setup_database(config)
        await init_db()

        # Verifica que as tabelas foram criadas
        from sqlalchemy import inspect

        engine = get_engine()
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )

        expected_tables = {
            "target_sites",
            "brand_assets",
            "monitoring_cycles",
            "screenshots",
            "detection_results",
            "alert_logs",
        }
        assert expected_tables.issubset(set(tables))

    async def test_init_db_sem_setup_levanta_runtime_error(self):
        """Deve levantar RuntimeError se setup não foi feito."""
        with pytest.raises(RuntimeError, match="Database não configurado"):
            await init_db()


class TestCloseDb:
    """Testes para close_db()."""

    async def test_close_db_sem_setup_nao_levanta_erro(self):
        """Deve ser seguro chamar close_db sem setup prévio."""
        await close_db()  # Não deve levantar exceção

    async def test_close_db_limpa_estado(self):
        """Deve limpar engine e session factory após fechar."""
        setup_database()
        await close_db()

        with pytest.raises(RuntimeError):
            get_engine()

        with pytest.raises(RuntimeError):
            get_session_factory()
