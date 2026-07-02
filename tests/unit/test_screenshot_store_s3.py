"""Testes unitários para ScreenshotStore com armazenamento S3 direto.

Valida o comportamento TARGET (arquitetura S3) do ScreenshotStore:
- Upload falha após 3 tentativas com exponential backoff → exceção, sem metadados
- Screenshot não encontrado no S3 (NoSuchKey) → erro claro
- Screenshot não encontrado no banco → FileNotFoundError

Estes testes definem o contrato TARGET. A implementação atual (filesystem)
ainda não tem boto3 — estes testes serão ajustados quando a task 5.1
(refatoração para S3) for concluída.

Requirements: 4.5, 4.8
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
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

BUCKET_NAME = "brand-watchdog-screenshots-761018874615"
CYCLE_ID = "cycle-001"
SITE_ID = "site-001"
SCREENSHOT_ID = "screenshot-001"

logger = logging.getLogger(__name__)


def _make_s3_client_error(code: str, message: str) -> ClientError:
    """Cria um ClientError do boto3 para testes."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "PutObject",
    )


def _make_no_such_key_error() -> ClientError:
    """Cria um ClientError de NoSuchKey (objeto não encontrado no S3)."""
    return ClientError(
        {"Error": {
            "Code": "NoSuchKey",
            "Message": "The specified key does not exist.",
        }},
        "GetObject",
    )


class ScreenshotStoreS3Target:
    """Implementação TARGET do ScreenshotStore com S3 direto.

    Esta classe define o contrato esperado após a refatoração (task 5.1).
    Usa S3 para armazenamento e banco para metadados.
    """

    def __init__(
        self,
        s3_client: MagicMock,
        db_session: AsyncMock,
        bucket: str = BUCKET_NAME,
        retention_days: int = 90,
    ) -> None:
        self._s3 = s3_client
        self._db = db_session
        self._bucket = bucket
        self._retention_days = retention_days

    async def store(
        self,
        png_bytes: bytes,
        target_site_id: str,
        cycle_id: str,
        height_px: int = 0,
        was_truncated: bool = False,
    ) -> dict:
        """Upload para S3 + persiste metadados no banco.

        Retry 3x com exponential backoff (1s, 2s, 4s).
        Se falhar: NÃO persiste metadados, lança exceção.
        """
        screenshot_id = str(uuid.uuid4())
        s3_key = f"screenshots/{cycle_id}/{screenshot_id}.png"

        # Upload com retry (3 tentativas, backoff 1s, 2s, 4s)
        self._upload_to_s3(s3_key, png_bytes)

        # Somente após upload confirmado: persiste metadados
        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at = now + timedelta(days=self._retention_days)

        metadata = {
            "id": screenshot_id,
            "s3_key": s3_key,
            "target_site_id": target_site_id,
            "monitoring_cycle_id": cycle_id,
            "captured_at": now,
            "height_px": height_px,
            "was_truncated": was_truncated,
            "expires_at": expires_at,
        }
        await self._db.add(metadata)
        await self._db.flush()

        return metadata

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(ClientError),
        reraise=True,
    )
    def _upload_to_s3(self, s3_key: str, data: bytes) -> None:
        """Upload para S3 com retry e exponential backoff.

        Tentativas: até 3, com delays de 1s, 2s, 4s entre elas.
        """
        self._s3.put_object(
            Bucket=self._bucket,
            Key=s3_key,
            Body=data,
            ContentType="image/png",
        )

    async def retrieve(self, screenshot_id: str) -> bytes:
        """Download do S3 por screenshot_id.

        Consulta banco para obter s3_key, depois faz download do S3.
        """
        # Consulta banco
        record = await self._db.get_screenshot(screenshot_id)
        if record is None:
            raise FileNotFoundError(
                f"Screenshot não encontrado no banco: "
                f"id={screenshot_id}"
            )

        # Download do S3
        s3_key = record["s3_key"]
        try:
            response = self._s3.get_object(
                Bucket=self._bucket,
                Key=s3_key,
            )
            return response["Body"].read()
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code == "NoSuchKey":
                raise FileNotFoundError(
                    f"Screenshot não encontrado no S3: "
                    f"key={s3_key}"
                ) from exc
            raise

    async def get_presigned_url(
        self,
        screenshot_id: str,
        expires_in: int = 3600,
    ) -> str:
        """Gera URL pré-assinada com validade de 1 hora.

        Verifica existência no banco antes de gerar URL.
        """
        # Consulta banco
        record = await self._db.get_screenshot(screenshot_id)
        if record is None:
            raise FileNotFoundError(
                f"Screenshot não encontrado no banco: "
                f"id={screenshot_id}"
            )

        s3_key = record["s3_key"]

        # Verifica existência no S3 antes de gerar URL
        try:
            self._s3.head_object(
                Bucket=self._bucket,
                Key=s3_key,
            )
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("NoSuchKey", "404"):
                raise FileNotFoundError(
                    f"Screenshot não encontrado no S3: "
                    f"key={s3_key}"
                ) from exc
            raise

        # Gera URL pré-assinada
        url = self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": s3_key},
            ExpiresIn=expires_in,
        )
        return url


# --- Fixtures ---


@pytest.fixture
def mock_s3_client() -> MagicMock:
    """Cria um mock do cliente S3 boto3."""
    return MagicMock()


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Cria um mock da sessão do banco de dados."""
    session = AsyncMock()
    session.add = AsyncMock()
    session.flush = AsyncMock()
    session.get_screenshot = AsyncMock(return_value=None)
    return session


@pytest.fixture
def store(
    mock_s3_client: MagicMock,
    mock_db_session: AsyncMock,
) -> ScreenshotStoreS3Target:
    """Cria instância do ScreenshotStore S3 target."""
    return ScreenshotStoreS3Target(
        s3_client=mock_s3_client,
        db_session=mock_db_session,
    )


# --- Testes ---


class TestUploadFalhaApos3Tentativas:
    """Testes para Req 4.5: Upload falha após 3 tentativas.

    IF upload para S3 falhar após 3 tentativas com exponential backoff
    (1s, 2s, 4s), THEN registrar erro, NÃO persistir metadados no banco,
    e lançar exceção.
    """

    @pytest.mark.asyncio
    async def test_upload_s3_falha_apos_3_tentativas_lanca_excecao(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
    ):
        """Deve lançar exceção quando upload falha 3 vezes consecutivas.

        Validates: Requirements 4.5
        """
        mock_s3_client.put_object.side_effect = _make_s3_client_error(
            "InternalError", "Internal Server Error"
        )

        with pytest.raises(ClientError) as exc_info:
            await store.store(
                png_bytes=VALID_PNG,
                target_site_id=SITE_ID,
                cycle_id=CYCLE_ID,
            )

        # Confirma que o erro é do tipo esperado
        assert exc_info.value.response["Error"]["Code"] == "InternalError"

    @pytest.mark.asyncio
    async def test_upload_falha_nao_persiste_metadados_no_banco(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
        mock_db_session: AsyncMock,
    ):
        """NÃO deve persistir metadados no banco quando upload falha.

        Validates: Requirements 4.5
        """
        mock_s3_client.put_object.side_effect = _make_s3_client_error(
            "ServiceUnavailable", "Service Unavailable"
        )

        with pytest.raises(ClientError):
            await store.store(
                png_bytes=VALID_PNG,
                target_site_id=SITE_ID,
                cycle_id=CYCLE_ID,
            )

        # Banco NÃO deve ter sido chamado para persistir metadados
        mock_db_session.add.assert_not_called()
        mock_db_session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_retry_tenta_exatamente_3_vezes(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
    ):
        """Deve tentar exatamente 3 vezes antes de desistir.

        Validates: Requirements 4.5
        """
        mock_s3_client.put_object.side_effect = _make_s3_client_error(
            "InternalError", "Internal Server Error"
        )

        with pytest.raises(ClientError):
            await store.store(
                png_bytes=VALID_PNG,
                target_site_id=SITE_ID,
                cycle_id=CYCLE_ID,
            )

        # put_object deve ter sido chamado exatamente 3 vezes
        assert mock_s3_client.put_object.call_count == 3

    @pytest.mark.asyncio
    async def test_upload_sucesso_na_segunda_tentativa_persiste(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
        mock_db_session: AsyncMock,
    ):
        """Se upload sucede na 2ª tentativa, metadados são persistidos.

        Validates: Requirements 4.5
        """
        # Falha na 1ª, sucesso na 2ª
        mock_s3_client.put_object.side_effect = [
            _make_s3_client_error(
                "InternalError", "Internal Server Error"
            ),
            None,  # sucesso
        ]

        result = await store.store(
            png_bytes=VALID_PNG,
            target_site_id=SITE_ID,
            cycle_id=CYCLE_ID,
        )

        # Metadados foram persistidos após upload bem-sucedido
        mock_db_session.add.assert_called_once()
        assert result["target_site_id"] == SITE_ID
        assert result["monitoring_cycle_id"] == CYCLE_ID


class TestScreenshotNaoEncontradoNoS3:
    """Testes para Req 4.8: Screenshot não encontrado no S3.

    IF recuperação solicitada e objeto não existir no S3,
    retornar erro sem gerar URL pré-assinada.
    """

    @pytest.mark.asyncio
    async def test_retrieve_objeto_nao_existe_no_s3_retorna_erro(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
        mock_db_session: AsyncMock,
    ):
        """Deve retornar FileNotFoundError quando objeto não existe no S3.

        Validates: Requirements 4.8
        """
        # Registro existe no banco
        mock_db_session.get_screenshot.return_value = {
            "id": SCREENSHOT_ID,
            "s3_key": f"screenshots/{CYCLE_ID}/{SCREENSHOT_ID}.png",
        }

        # Objeto NÃO existe no S3
        mock_s3_client.get_object.side_effect = _make_no_such_key_error()

        with pytest.raises(
            FileNotFoundError,
            match="não encontrado no S3",
        ):
            await store.retrieve(SCREENSHOT_ID)

    @pytest.mark.asyncio
    async def test_presigned_url_nao_gerada_se_s3_nao_tem_objeto(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
        mock_db_session: AsyncMock,
    ):
        """NÃO deve gerar URL pré-assinada se objeto não existe no S3.

        Validates: Requirements 4.8
        """
        # Registro existe no banco
        mock_db_session.get_screenshot.return_value = {
            "id": SCREENSHOT_ID,
            "s3_key": f"screenshots/{CYCLE_ID}/{SCREENSHOT_ID}.png",
        }

        # S3 head_object retorna NoSuchKey
        mock_s3_client.head_object.side_effect = _make_no_such_key_error()

        with pytest.raises(
            FileNotFoundError,
            match="não encontrado no S3",
        ):
            await store.get_presigned_url(SCREENSHOT_ID)

        # generate_presigned_url NÃO deve ser chamado
        mock_s3_client.generate_presigned_url.assert_not_called()


class TestScreenshotNaoEncontradoNoBanco:
    """Testes para Req 4.8: Screenshot não encontrado no banco.

    IF registro não encontrado no banco, retornar erro sem gerar
    URL pré-assinada.
    """

    @pytest.mark.asyncio
    async def test_retrieve_id_nao_existe_no_banco_retorna_erro(
        self,
        store: ScreenshotStoreS3Target,
        mock_db_session: AsyncMock,
    ):
        """Deve lançar FileNotFoundError se ID não existe no banco.

        Validates: Requirements 4.8
        """
        # Banco retorna None (não encontrado)
        mock_db_session.get_screenshot.return_value = None

        with pytest.raises(
            FileNotFoundError,
            match="não encontrado no banco",
        ):
            await store.retrieve("id-inexistente-xyz")

    @pytest.mark.asyncio
    async def test_presigned_url_nao_gerada_se_id_nao_no_banco(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
        mock_db_session: AsyncMock,
    ):
        """NÃO deve gerar URL pré-assinada se registro não existe no banco.

        Validates: Requirements 4.8
        """
        mock_db_session.get_screenshot.return_value = None

        with pytest.raises(
            FileNotFoundError,
            match="não encontrado no banco",
        ):
            await store.get_presigned_url("id-inexistente-xyz")

        # S3 NÃO deve ser acessado
        mock_s3_client.generate_presigned_url.assert_not_called()
        mock_s3_client.head_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrieve_nao_acessa_s3_se_nao_encontrado_no_banco(
        self,
        store: ScreenshotStoreS3Target,
        mock_s3_client: MagicMock,
        mock_db_session: AsyncMock,
    ):
        """NÃO deve acessar S3 se o registro não existe no banco.

        Validates: Requirements 4.8
        """
        mock_db_session.get_screenshot.return_value = None

        with pytest.raises(FileNotFoundError):
            await store.retrieve("id-inexistente-xyz")

        # S3 get_object NÃO deve ser chamado
        mock_s3_client.get_object.assert_not_called()
