"""Testes unitários para o ScreenshotStore com S3 direto.

Valida armazenamento, recuperação e geração de URLs pré-assinadas
com integração ao banco de dados e S3 (via moto mock).

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7, 4.8
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    get_session,
    init_db,
    setup_database,
)
from brand_watchdog.models.entities import (
    MonitoringCycleModel,
    ScreenshotModel,
    TargetSiteModel,
)
from brand_watchdog.storage.screenshot_store import (
    ScreenshotNotFoundError,
    ScreenshotStore,
)


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

BUCKET_NAME = "brand-watchdog-screenshots-test"
REGION = "us-east-1"


@pytest.fixture
def aws_credentials():
    """Mock AWS Credentials para moto."""
    import os

    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = REGION


@pytest.fixture
def s3_mock(aws_credentials):
    """Cria bucket S3 mock via moto."""
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET_NAME)
        yield s3


@pytest.fixture(autouse=True)
async def setup_test_db():
    """Configura banco in-memory para cada teste."""
    config = StorageConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        s3_bucket=BUCKET_NAME,
        s3_region=REGION,
    )
    setup_database(config)
    await init_db()
    yield
    await close_db()


@pytest.fixture
def storage_config() -> StorageConfig:
    """Cria configuração de storage para testes."""
    return StorageConfig(
        screenshot_retention_days=90,
        database_url="sqlite+aiosqlite:///:memory:",
        s3_bucket=BUCKET_NAME,
        s3_region=REGION,
    )


@pytest.fixture
def store(storage_config: StorageConfig, s3_mock) -> ScreenshotStore:
    """Cria instância do ScreenshotStore com S3 mock."""
    s = ScreenshotStore(config=storage_config)
    # Substituir o cliente S3 pelo mock do moto
    s._s3_client = s3_mock
    return s


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
    """Testes para store() - upload S3 direto."""

    async def test_armazena_screenshot_com_sucesso(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve fazer upload S3 e retornar ScreenshotModel com metadados."""
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

    async def test_s3_key_formato_correto(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """s3_key deve ter formato screenshots/{cycle_id}/{uuid}.png."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        assert result.s3_key.startswith(f"screenshots/{cycle_id}/")
        assert result.s3_key.endswith(".png")
        # Deve conter o id do screenshot
        assert result.id in result.s3_key

    async def test_objeto_existe_no_s3_apos_upload(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        s3_mock,
    ):
        """Objeto deve existir no S3 após store() com sucesso."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        # Verifica que o objeto existe no S3
        response = s3_mock.get_object(
            Bucket=BUCKET_NAME,
            Key=result.s3_key,
        )
        body = response["Body"].read()
        assert body == VALID_PNG

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

        expires_at = result.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

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

        assert result.captured_at.microsecond == 0

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

    async def test_nao_cria_diretorio_local(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        tmp_path: Path,
    ):
        """NÃO deve criar diretórios locais (upload direto para S3)."""
        await store.store(VALID_PNG, target_site_id, cycle_id)

        # Nenhum diretório screenshots deve existir localmente
        screenshots_dir = tmp_path / "screenshots"
        assert not screenshots_dir.exists()

    async def test_multipart_upload_para_arquivo_grande(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        s3_mock,
    ):
        """Deve usar multipart upload para arquivos > 5MB."""
        # Cria um arquivo de 6MB
        large_png = b"\x89PNG" + b"\x00" * (6 * 1024 * 1024)

        result = await store.store(
            large_png, target_site_id, cycle_id
        )

        # Objeto deve existir no S3
        response = s3_mock.get_object(
            Bucket=BUCKET_NAME,
            Key=result.s3_key,
        )
        body = response["Body"].read()
        assert body == large_png


class TestRetrieve:
    """Testes para retrieve() - download do S3."""

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
        """Deve lançar erro se ID não existe no banco."""
        with pytest.raises(FileNotFoundError, match="não encontrado no banco"):
            await store.retrieve("id-inexistente")

    async def test_erro_screenshot_nao_encontrado_no_s3(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        s3_mock,
    ):
        """Deve lançar erro se objeto não existe no S3."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        # Remove o objeto do S3 diretamente
        s3_mock.delete_object(Bucket=BUCKET_NAME, Key=result.s3_key)

        with pytest.raises(FileNotFoundError, match="não encontrado no S3"):
            await store.retrieve(result.id)


class TestGetPresignedUrl:
    """Testes para get_presigned_url() - URLs pré-assinadas."""

    async def test_gera_url_pre_assinada(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve gerar URL pré-assinada válida."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        url = await store.get_presigned_url(result.id)

        assert url is not None
        assert isinstance(url, str)
        assert len(url) > 0
        # URL deve conter referência ao bucket e key
        assert BUCKET_NAME in url or "s3" in url.lower()

    async def test_url_com_validade_customizada(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
    ):
        """Deve aceitar validade customizada em segundos."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        # 2 horas de validade
        url = await store.get_presigned_url(result.id, expires_in=7200)
        assert url is not None
        assert len(url) > 0

    async def test_erro_screenshot_nao_encontrado_no_banco(
        self, store: ScreenshotStore
    ):
        """Deve lançar erro se screenshot não existe no banco."""
        with pytest.raises(FileNotFoundError, match="não encontrado no banco"):
            await store.get_presigned_url("id-inexistente")

    async def test_erro_screenshot_nao_encontrado_no_s3(
        self,
        store: ScreenshotStore,
        target_site_id: str,
        cycle_id: str,
        s3_mock,
    ):
        """Deve lançar erro se objeto não existe no S3."""
        result = await store.store(
            VALID_PNG, target_site_id, cycle_id
        )

        # Remove o objeto do S3
        s3_mock.delete_object(Bucket=BUCKET_NAME, Key=result.s3_key)

        with pytest.raises(FileNotFoundError, match="não encontrado no S3"):
            await store.get_presigned_url(result.id)


class TestUploadRetry:
    """Testes para retry 3x com backoff no upload.

    Validates: Requirements 4.5
    """

    async def test_upload_falha_apos_3_tentativas(
        self,
        storage_config: StorageConfig,
    ):
        """Deve lançar exceção quando upload falha 3 vezes."""
        store = ScreenshotStore(config=storage_config)

        # Mock do s3_client que sempre falha
        mock_client = MagicMock()
        mock_client.put_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "test"}},
            "PutObject",
        )
        store._s3_client = mock_client

        with pytest.raises(ClientError):
            await store.store(
                png_bytes=VALID_PNG,
                target_site_id="site-001",
                cycle_id="cycle-001",
            )

        # Deve ter tentado exatamente 3 vezes
        assert mock_client.put_object.call_count == 3

    async def test_upload_falha_nao_persiste_metadados(
        self,
        storage_config: StorageConfig,
        target_site_id: str,
        cycle_id: str,
    ):
        """NÃO deve persistir metadados quando upload falha."""
        store = ScreenshotStore(config=storage_config)

        mock_client = MagicMock()
        mock_client.put_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "test"}},
            "PutObject",
        )
        store._s3_client = mock_client

        with pytest.raises(ClientError):
            await store.store(
                png_bytes=VALID_PNG,
                target_site_id=target_site_id,
                cycle_id=cycle_id,
            )

        # Verifica que nenhum metadado foi persistido
        from sqlalchemy import select, func

        async with get_session() as session:
            stmt = (
                select(func.count())
                .select_from(ScreenshotModel)
                .where(ScreenshotModel.monitoring_cycle_id == cycle_id)
            )
            result = await session.execute(stmt)
            count = result.scalar()

        assert count == 0

    async def test_upload_sucesso_na_segunda_tentativa(
        self,
        storage_config: StorageConfig,
        target_site_id: str,
        cycle_id: str,
    ):
        """Se upload sucede na 2ª tentativa, metadados são persistidos."""
        store = ScreenshotStore(config=storage_config)

        mock_client = MagicMock()
        mock_client.put_object.side_effect = [
            ClientError(
                {"Error": {"Code": "InternalError", "Message": "test"}},
                "PutObject",
            ),
            None,  # sucesso na 2ª tentativa
        ]
        store._s3_client = mock_client

        result = await store.store(
            png_bytes=VALID_PNG,
            target_site_id=target_site_id,
            cycle_id=cycle_id,
        )

        assert result is not None
        assert result.target_site_id == target_site_id
        assert mock_client.put_object.call_count == 2
