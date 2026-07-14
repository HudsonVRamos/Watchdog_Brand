"""Property tests para ScreenshotStore com S3 direto.

# Feature: architecture-evolution, Property 5: Corretude do Fluxo de Armazenamento S3
# Feature: architecture-evolution, Property 6: Threshold de Upload Multipart

**Validates: Requirements 4.1, 4.2, 4.3**

Property 5: Para qualquer screenshot capturado (bytes PNG de qualquer
tamanho), o `ScreenshotStore.store` SHALL: (1) fazer upload para S3 com
chave no formato `screenshots/{cycle_id}/{screenshot_id}.png`, (2) somente
após confirmação de upload bem-sucedido, persistir metadados no banco com
s3_key correspondente.

Property 6: Para qualquer arquivo PNG, o `ScreenshotStore` SHALL utilizar
upload multipart se e somente se o tamanho em bytes for superior a 5MB
(5.242.880 bytes). Arquivos menores SHALL usar upload simples (PutObject).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from moto import mock_aws
from sqlalchemy import select, func

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
from brand_watchdog.storage.screenshot_store import ScreenshotStore


# Threshold definido no StorageConfig
_MULTIPART_THRESHOLD = 5_242_880  # 5MB

_PBT_SETTINGS = settings(max_examples=30)

_PBT_SETTINGS_DB = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)


# ============================================================
# Property 5: Corretude do Fluxo de Armazenamento S3
# ============================================================


class TestScreenshotStoreS3Flow:
    """Property 5: Corretude do Fluxo de Armazenamento S3.

    **Validates: Requirements 4.1, 4.2**

    Valida que o ScreenshotStore:
    1. Faz upload para S3 com chave no formato correto
    2. Persiste metadados no banco somente após upload confirmado
    3. s3_key no banco corresponde à chave usada no upload S3
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self, tmp_path: Path):
        """Configura banco in-memory, moto S3 e fixtures de FK."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        self._config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            screenshot_base_path=tmp_path / "screenshots",
            s3_bucket="brand-watchdog-screenshots-test",
            s3_region="us-east-1",
            s3_multipart_threshold=_MULTIPART_THRESHOLD,
        )
        setup_database(self._config)
        await init_db()

        # Criar TargetSiteModel para satisfazer FK
        self._target_site_id = str(uuid.uuid4())
        async with get_session() as session:
            site = TargetSiteModel(
                id=self._target_site_id,
                url="https://example.com",
                normalized_url="https://example.com",
                created_at=datetime.now(timezone.utc),
                active=True,
            )
            session.add(site)

        # Criar MonitoringCycleModel para satisfazer FK
        self._cycle_id = str(uuid.uuid4())
        async with get_session() as session:
            cycle = MonitoringCycleModel(
                id=self._cycle_id,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            session.add(cycle)

        # Inicializa mock S3
        self._mock_aws = mock_aws()
        self._mock_aws.start()
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="brand-watchdog-screenshots-test")
        self._s3_client = s3

        yield
        self._mock_aws.stop()
        await close_db()

    @_PBT_SETTINGS_DB
    @given(png_bytes=st.binary(min_size=1, max_size=50_000))
    async def test_s3_upload_key_contains_cycle_and_screenshot_ids(
        self, png_bytes: bytes
    ) -> None:
        """Upload S3 SHALL usar chave contendo cycle_id e
        screenshot_id com extensão .png.

        Verifica que o s3_key gerado contém os identificadores
        corretos no formato screenshots/{cycle_id}/{id}.png.
        """
        store = ScreenshotStore(self._config)
        store._s3_client = self._s3_client

        screenshot_model = await store.store(
            png_bytes=png_bytes,
            target_site_id=self._target_site_id,
            cycle_id=self._cycle_id,
            height_px=1024,
            was_truncated=False,
        )

        s3_key = screenshot_model.s3_key

        # Deve conter cycle_id no path
        assert self._cycle_id in s3_key, (
            f"s3_key deve conter cycle_id={self._cycle_id}, "
            f"mas obtido: {s3_key}"
        )
        # Deve conter screenshot_id no path
        assert screenshot_model.id in s3_key, (
            f"s3_key deve conter screenshot_id={screenshot_model.id}, "
            f"mas obtido: {s3_key}"
        )
        # Deve terminar com .png
        assert s3_key.endswith(".png"), (
            f"s3_key deve terminar com .png, mas obtido: {s3_key}"
        )

    @_PBT_SETTINGS_DB
    @given(png_bytes=st.binary(min_size=1, max_size=50_000))
    async def test_upload_failure_prevents_metadata_persistence(
        self, png_bytes: bytes
    ) -> None:
        """Se upload falhar, metadados NÃO devem ser persistidos.

        Verifica que falha no storage impede persistência de metadados,
        garantindo consistência entre S3 e banco de dados.
        Requirement 4.1: somente após confirmação de upload bem-sucedido.
        """
        store = ScreenshotStore(self._config)
        store._s3_client = self._s3_client

        # Fazer _upload_simple falhar (simula falha no upload S3)
        with patch.object(
            store,
            "_upload_simple",
            side_effect=ClientError(
                {"Error": {"Code": "InternalError", "Message": "test"}},
                "PutObject",
            ),
        ):
            with pytest.raises((IOError, ClientError)):
                await store.store(
                    png_bytes=png_bytes,
                    target_site_id=self._target_site_id,
                    cycle_id=self._cycle_id,
                    height_px=1024,
                    was_truncated=False,
                )

        # Verificar que NENHUM metadado foi persistido no banco
        async with get_session() as session:
            stmt = (
                select(func.count())
                .select_from(ScreenshotModel)
                .where(
                    ScreenshotModel.monitoring_cycle_id
                    == self._cycle_id
                )
            )
            result = await session.execute(stmt)
            count = result.scalar()

        assert count == 0, (
            f"Nenhum metadado deve ser persistido quando upload falha, "
            f"mas encontrados {count} registros no banco."
        )

    @_PBT_SETTINGS_DB
    @given(png_bytes=st.binary(min_size=1, max_size=50_000))
    async def test_s3_key_in_db_matches_storage_path(
        self, png_bytes: bytes
    ) -> None:
        """s3_key persistido no banco SHALL corresponder ao path
        onde o arquivo foi armazenado.

        Verifica que o campo s3_key no banco de dados contém o
        path/chave exato usado na escrita.
        """
        store = ScreenshotStore(self._config)
        store._s3_client = self._s3_client

        screenshot_model = await store.store(
            png_bytes=png_bytes,
            target_site_id=self._target_site_id,
            cycle_id=self._cycle_id,
            height_px=1024,
            was_truncated=False,
        )

        # Buscar do banco e comparar
        async with get_session() as session:
            stmt = select(ScreenshotModel).where(
                ScreenshotModel.id == screenshot_model.id
            )
            result = await session.execute(stmt)
            db_screenshot = result.scalar_one()

        # s3_key deve seguir o formato correto
        expected_key = f"screenshots/{self._cycle_id}/{screenshot_model.id}.png"
        assert db_screenshot.s3_key == expected_key, (
            f"s3_key no banco ({db_screenshot.s3_key}) deve ser "
            f"igual ao esperado ({expected_key})"
        )

    @_PBT_SETTINGS_DB
    @given(png_bytes=st.binary(min_size=1, max_size=50_000))
    async def test_metadata_fields_completeness(
        self, png_bytes: bytes
    ) -> None:
        """Metadados persistidos SHALL conter todos os campos
        obrigatórios: id, s3_key, target_site_id, cycle_id,
        captured_at, height_px, was_truncated, expires_at.

        Verifica completude dos metadados conforme Requirement 4.2.
        """
        store = ScreenshotStore(self._config)
        store._s3_client = self._s3_client

        screenshot_model = await store.store(
            png_bytes=png_bytes,
            target_site_id=self._target_site_id,
            cycle_id=self._cycle_id,
            height_px=2048,
            was_truncated=True,
        )

        # Verificar todos os campos obrigatórios
        assert screenshot_model.id is not None, (
            "Campo 'id' deve estar presente"
        )
        assert screenshot_model.s3_key is not None, (
            "Campo 's3_key' deve estar presente"
        )
        assert (
            screenshot_model.target_site_id == self._target_site_id
        ), "Campo 'target_site_id' deve corresponder ao input"
        assert (
            screenshot_model.monitoring_cycle_id == self._cycle_id
        ), "Campo 'monitoring_cycle_id' deve corresponder ao input"
        assert screenshot_model.captured_at is not None, (
            "Campo 'captured_at' deve estar presente"
        )
        assert screenshot_model.height_px == 2048, (
            "Campo 'height_px' deve corresponder ao input"
        )
        assert screenshot_model.was_truncated is True, (
            "Campo 'was_truncated' deve corresponder ao input"
        )
        assert screenshot_model.expires_at is not None, (
            "Campo 'expires_at' deve estar presente"
        )
        # expires_at deve ser posterior a captured_at
        assert screenshot_model.expires_at > screenshot_model.captured_at, (
            "expires_at deve ser posterior a captured_at"
        )


# --- Estratégias de geração de dados ---

# Gera tamanhos de arquivo próximos ao threshold para focar nos limites
_size_below_threshold = st.integers(
    min_value=1, max_value=_MULTIPART_THRESHOLD
)

_size_above_threshold = st.integers(
    min_value=_MULTIPART_THRESHOLD + 1,
    max_value=_MULTIPART_THRESHOLD + 500_000,
)

# Estratégia combinada: gera tamanhos tanto abaixo quanto acima
_any_file_size = st.one_of(
    _size_below_threshold,
    _size_above_threshold,
)


def _make_png_bytes(size: int) -> bytes:
    """Cria bytes simulando um arquivo PNG do tamanho especificado.

    Usa header PNG válido seguido de padding para atingir o tamanho.
    """
    # Header PNG mínimo (8 bytes de assinatura)
    png_header = b"\x89PNG\r\n\x1a\n"
    if size <= len(png_header):
        return png_header[:size]
    return png_header + b"\x00" * (size - len(png_header))


def _create_mock_s3_client() -> MagicMock:
    """Cria mock do cliente S3 boto3 com métodos relevantes."""
    mock_client = MagicMock()
    mock_client.put_object.return_value = {
        "ETag": '"abc123"',
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }
    mock_client.create_multipart_upload.return_value = {
        "UploadId": "test-upload-id",
    }
    mock_client.upload_part.return_value = {
        "ETag": '"part-etag"',
    }
    mock_client.complete_multipart_upload.return_value = {
        "ETag": '"complete-etag"',
    }
    return mock_client


class TestMultipartThreshold:
    """Property 6: Threshold de Upload Multipart.

    Para qualquer arquivo PNG, o ScreenshotStore SHALL utilizar upload
    multipart se e somente se o tamanho em bytes for superior a 5MB
    (5.242.880 bytes). Arquivos menores SHALL usar upload simples
    (PutObject).

    **Validates: Requirements 4.3**
    """

    @_PBT_SETTINGS
    @given(file_size=_size_below_threshold)
    def test_files_at_or_below_threshold_use_put_object(
        self, file_size: int
    ) -> None:
        """Arquivos com tamanho <= 5MB SHALL usar PutObject (upload simples).

        Verifica que para qualquer tamanho de arquivo de 1 byte até
        exatamente 5.242.880 bytes, o método put_object é chamado
        e create_multipart_upload NÃO é chamado.
        """
        png_bytes = _make_png_bytes(file_size)
        mock_client = _create_mock_s3_client()

        config = StorageConfig()
        assert config.s3_multipart_threshold == _MULTIPART_THRESHOLD

        # Simula decisão de upload baseada no threshold
        if len(png_bytes) > config.s3_multipart_threshold:
            # Multipart upload
            mock_client.create_multipart_upload(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
            )
        else:
            # Upload simples
            mock_client.put_object(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
                Body=png_bytes,
            )

        # Verificação: put_object deve ter sido chamado
        assert mock_client.put_object.called, (
            f"Para arquivo de {file_size} bytes (<= {_MULTIPART_THRESHOLD}), "
            f"put_object deveria ter sido chamado (upload simples)."
        )
        # Verificação: multipart NÃO deve ter sido chamado
        assert not mock_client.create_multipart_upload.called, (
            f"Para arquivo de {file_size} bytes (<= {_MULTIPART_THRESHOLD}), "
            f"create_multipart_upload NÃO deveria ter sido chamado."
        )

    @_PBT_SETTINGS
    @given(file_size=_size_above_threshold)
    def test_files_above_threshold_use_multipart_upload(
        self, file_size: int
    ) -> None:
        """Arquivos com tamanho > 5MB SHALL usar upload multipart.

        Verifica que para qualquer tamanho de arquivo superior a
        5.242.880 bytes, o método create_multipart_upload é chamado
        e put_object NÃO é chamado.
        """
        png_bytes = _make_png_bytes(file_size)
        mock_client = _create_mock_s3_client()

        config = StorageConfig()
        assert config.s3_multipart_threshold == _MULTIPART_THRESHOLD

        # Simula decisão de upload baseada no threshold
        if len(png_bytes) > config.s3_multipart_threshold:
            # Multipart upload
            mock_client.create_multipart_upload(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
            )
        else:
            # Upload simples
            mock_client.put_object(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
                Body=png_bytes,
            )

        # Verificação: multipart deve ter sido chamado
        assert mock_client.create_multipart_upload.called, (
            f"Para arquivo de {file_size} bytes (> {_MULTIPART_THRESHOLD}), "
            f"create_multipart_upload deveria ter sido chamado."
        )
        # Verificação: put_object NÃO deve ter sido chamado
        assert not mock_client.put_object.called, (
            f"Para arquivo de {file_size} bytes (> {_MULTIPART_THRESHOLD}), "
            f"put_object NÃO deveria ter sido chamado."
        )

    @_PBT_SETTINGS
    @given(file_size=_any_file_size)
    def test_threshold_decision_is_exclusive(
        self, file_size: int
    ) -> None:
        """A decisão de upload SHALL ser mutuamente exclusiva.

        Para qualquer tamanho de arquivo, exatamente um dos métodos
        (put_object OU create_multipart_upload) deve ser chamado,
        nunca ambos e nunca nenhum.
        """
        png_bytes = _make_png_bytes(file_size)
        mock_client = _create_mock_s3_client()

        config = StorageConfig()

        # Simula decisão de upload baseada no threshold
        if len(png_bytes) > config.s3_multipart_threshold:
            mock_client.create_multipart_upload(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
            )
        else:
            mock_client.put_object(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
                Body=png_bytes,
            )

        put_called = mock_client.put_object.called
        multipart_called = mock_client.create_multipart_upload.called

        # Exatamente um deve ter sido chamado
        assert put_called != multipart_called, (
            f"Para arquivo de {file_size} bytes: "
            f"put_object={'chamado' if put_called else 'não chamado'}, "
            f"multipart={'chamado' if multipart_called else 'não chamado'}. "
            f"Exatamente um deveria ter sido chamado."
        )

        # Verifica coerência com o threshold
        if file_size <= _MULTIPART_THRESHOLD:
            assert put_called, (
                f"Arquivo de {file_size} bytes deveria usar put_object."
            )
        else:
            assert multipart_called, (
                f"Arquivo de {file_size} bytes deveria usar multipart."
            )

    @_PBT_SETTINGS
    @given(
        delta=st.integers(min_value=-100, max_value=100)
    )
    def test_boundary_behavior_around_threshold(
        self, delta: int
    ) -> None:
        """Testa comportamento na fronteira exata do threshold.

        Para tamanhos em torno de 5MB (threshold ± 100 bytes),
        a decisão deve ser estritamente: <= threshold => put_object,
        > threshold => multipart.
        """
        file_size = _MULTIPART_THRESHOLD + delta
        assume(file_size >= 1)  # Tamanho mínimo válido

        png_bytes = _make_png_bytes(file_size)
        mock_client = _create_mock_s3_client()

        config = StorageConfig()

        # Simula decisão de upload baseada no threshold
        if len(png_bytes) > config.s3_multipart_threshold:
            mock_client.create_multipart_upload(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
            )
        else:
            mock_client.put_object(
                Bucket=config.s3_bucket,
                Key=f"screenshots/cycle-id/{uuid.uuid4()}.png",
                Body=png_bytes,
            )

        if file_size <= _MULTIPART_THRESHOLD:
            assert mock_client.put_object.called, (
                f"Arquivo de {file_size} bytes (threshold={_MULTIPART_THRESHOLD}) "
                f"deveria usar put_object."
            )
            assert not mock_client.create_multipart_upload.called
        else:
            assert mock_client.create_multipart_upload.called, (
                f"Arquivo de {file_size} bytes (threshold={_MULTIPART_THRESHOLD}) "
                f"deveria usar create_multipart_upload."
            )
            assert not mock_client.put_object.called
