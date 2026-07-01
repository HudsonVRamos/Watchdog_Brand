"""Testes unitários para o ScreenshotStore.

Valida armazenamento, recuperação e cleanup de screenshots
com integração ao banco de dados e filesystem.
"""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    init_db,
    setup_database,
    get_session,
)
from brand_watchdog.models.entities import (
    MonitoringCycleModel,
    ScreenshotModel,
    TargetSiteModel,
)
from brand_watchdog.storage.screenshot_store import ScreenshotStore


# PNG mínimo válido (1x1 pixel)
VALID_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx"
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
    b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
async def setup_test_db():
    """Configura banco in-memory para cada teste."""
    config = StorageConfig(
        database_url="sqlite+aiosqlite:///:memory:"
    )
    setup_database(config)
    await init_db()
    yield
    await close_db()


@pytest.fixture
def screenshot_path(tmp_path: Path) -> Path:
    """Retorna diretório temporário para screenshots."""
    return tmp_path / "screenshots"


@pytest.fixture
def storage_config(screenshot_path: Path) -> StorageConfig:
    """Cria configuração de storage com path temporário."""
    return StorageConfig(
        screenshot_base_path=screenshot_path,
        screenshot_retention_days=90,
        database_url="sqlite+aiosqlite:///:memory:",
    )


@pytest.fixture
def store(storage_config: StorageConfig) -> ScreenshotStore:
    """Cria instância do ScreenshotStore com config temporária."""
    return ScreenshotStore(config=storage_config)


@pytest.fixture
async def target_site_id() -> str:
    """Cria um Target Site no banco e retorna o ID."""
    async with get_session() as session:
        site = TargetSiteModel(
            id="site-001",
            url="https://example.com",
            normalized_url="https://example.com",
            active=True,
        )
        session.add(site)
        await session.flush()
    return "site-001"


@pytest.fixture
async def cycle_id() -> str:
    """Cria um MonitoringCycle no banco e retorna o ID."""
    async with get_session() as session:
        cycle = MonitoringCycleModel(
            id="cycle-001",
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        session.add(cycle)
        await session.flush()
    return "cycle-001"


class TestStore:
    """Testes para store()."""

    async def test_armazena_screenshot_com_sucesso(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve armazenar PNG e retornar ScreenshotModel com metadados."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id, height_px=1080
        )

        assert result.id is not None
        assert result.target_site_id == target_site_id
        assert result.monitoring_cycle_id == cycle_id
        assert result.height_px == 1080
        assert result.was_truncated is False
        assert result.captured_at is not None
        assert result.expires_at is not None

    async def test_arquivo_criado_no_filesystem(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        screenshot_path: Path,
    ):
        """Deve criar arquivo PNG no diretório do ciclo."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        file_path = Path(result.file_path)
        assert file_path.exists()
        assert file_path.read_bytes() == VALID_PNG

    async def test_path_segue_padrao_cycle_uuid(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        screenshot_path: Path,
    ):
        """Deve criar arquivo em {base_path}/{cycle_id}/{uuid}.png."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        file_path = Path(result.file_path)
        assert file_path.parent.name == cycle_id
        assert file_path.suffix == ".png"
        assert file_path.parent.parent == screenshot_path

    async def test_expires_at_baseado_em_retention_days(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve calcular expires_at = now + retention_days."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        expected_min = datetime.now(timezone.utc) + timedelta(days=89)
        expected_max = datetime.now(timezone.utc) + timedelta(days=91)

        # expires_at deve ser ~90 dias a partir de agora
        expires_naive = result.expires_at
        if expires_naive.tzinfo is None:
            expires_at = expires_naive.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_naive

        assert expected_min <= expires_at <= expected_max

    async def test_captured_at_em_utc_sem_microsegundos(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve armazenar captured_at em UTC com precisão de segundos."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        captured = result.captured_at
        # Precisão de segundos (microsecond == 0)
        assert captured.microsecond == 0

    async def test_armazena_was_truncated_true(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve registrar was_truncated=True quando informado."""
        result = await store.store(
            VALID_PNG,
            target_site_id,
            cycle_id,
            height_px=20000,
            was_truncated=True,
        )

        assert result.was_truncated is True
        assert result.height_px == 20000

    async def test_cria_diretorio_ciclo_se_nao_existe(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        screenshot_path: Path,
    ):
        """Deve criar diretório do ciclo automaticamente."""
        assert not screenshot_path.exists()

        await store.store(VALID_PNG, target_site_id, cycle_id)

        assert (screenshot_path / cycle_id).exists()


class TestRetrieve:
    """Testes para retrieve()."""

    async def test_recupera_bytes_identicos(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve retornar bytes idênticos ao armazenado."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        retrieved = await store.retrieve(result.id)
        assert retrieved == VALID_PNG

    async def test_erro_screenshot_nao_encontrado_no_banco(
        self, store: ScreenshotStore
    ):
        """Deve lançar FileNotFoundError se ID não existe no banco."""
        with pytest.raises(FileNotFoundError, match="não encontrado no banco"):
            await store.retrieve("id-inexistente")

    async def test_erro_arquivo_nao_existe_no_filesystem(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve lançar FileNotFoundError se arquivo físico foi removido."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        # Remove arquivo manualmente
        Path(result.file_path).unlink()

        with pytest.raises(FileNotFoundError, match="não encontrado"):
            await store.retrieve(result.id)


class TestCleanupExpired:
    """Testes para cleanup_expired()."""

    async def test_remove_screenshots_expirados(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve remover screenshots com expires_at no passado."""
        # Armazena um screenshot
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        # Atualiza expires_at para o passado manualmente
        async with get_session() as session:
            from sqlalchemy import select, update
            stmt = (
                update(ScreenshotModel)
                .where(ScreenshotModel.id == result.id)
                .values(expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
            )
            await session.execute(stmt)

        # Executa cleanup
        removed = await store.cleanup_expired()

        assert removed == 1
        # Arquivo deve ter sido deletado
        assert not Path(result.file_path).exists()

    async def test_nao_remove_screenshots_validos(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Não deve remover screenshots que ainda não expiraram."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        removed = await store.cleanup_expired()

        assert removed == 0
        # Arquivo deve continuar existindo
        assert Path(result.file_path).exists()

    async def test_retorna_zero_sem_expirados(
        self, store: ScreenshotStore
    ):
        """Deve retornar 0 quando não há screenshots expirados."""
        removed = await store.cleanup_expired()
        assert removed == 0

    async def test_cleanup_remove_arquivo_fisico(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve deletar arquivo físico junto com registro do banco."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )
        file_path = Path(result.file_path)
        assert file_path.exists()

        # Expira o screenshot
        async with get_session() as session:
            from sqlalchemy import update
            stmt = (
                update(ScreenshotModel)
                .where(ScreenshotModel.id == result.id)
                .values(expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
            )
            await session.execute(stmt)

        await store.cleanup_expired()

        assert not file_path.exists()

    async def test_cleanup_continua_se_arquivo_ja_removido(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve continuar cleanup mesmo se arquivo já foi deletado."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        # Remove arquivo manualmente
        Path(result.file_path).unlink()

        # Expira o screenshot
        async with get_session() as session:
            from sqlalchemy import update
            stmt = (
                update(ScreenshotModel)
                .where(ScreenshotModel.id == result.id)
                .values(expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
            )
            await session.execute(stmt)

        # Não deve lançar exceção
        removed = await store.cleanup_expired()
        assert removed == 1
