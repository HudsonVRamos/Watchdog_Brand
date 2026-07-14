"""Armazenamento de screenshots diretamente no S3.

Gerencia o ciclo de vida de screenshots capturados:
- Upload direto para S3 (sem filesystem local)
- Upload multipart para arquivos > 5MB (5.242.880 bytes)
- Retry 3x com exponential backoff (1s, 2s, 4s)
- URLs pré-assinadas com validade configurável (padrão 1 hora)
- Associação com Target_Site e ciclo de monitoramento
- Cálculo de expiração baseado em configuração
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import select
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import get_session
from brand_watchdog.models.entities import ScreenshotModel

logger = logging.getLogger(__name__)

# Threshold para multipart upload (5MB)
_MULTIPART_THRESHOLD = 5_242_880

# Tamanho de cada parte no multipart upload (5MB)
_MULTIPART_PART_SIZE = 5_242_880

# Tamanho do batch para operações de cleanup
_CLEANUP_BATCH_SIZE = 100


class ScreenshotStoreError(Exception):
    """Erro genérico do ScreenshotStore."""

    pass


class ScreenshotNotFoundError(FileNotFoundError):
    """Screenshot não encontrado no S3 ou no banco de dados."""

    pass


class ScreenshotStore:
    """Gerencia armazenamento e recuperação de screenshots no S3.

    Responsabilidades:
        - Upload direto para S3 com retry e exponential backoff
        - Upload multipart para arquivos > 5MB
        - Geração de URLs pré-assinadas com validade de 1 hora
        - Download de screenshots do S3 por screenshot_id
        - Persistência de metadados SOMENTE após upload confirmado
        - Tratamento claro de screenshots não encontrados

    Args:
        config: Configuração de storage com bucket S3 e retention days.
    """

    def __init__(self, config: StorageConfig) -> None:
        self._bucket = config.s3_bucket
        self._region = config.s3_region
        self._retention_days = config.screenshot_retention_days
        self._multipart_threshold = config.s3_multipart_threshold
        self._s3_client = boto3.client("s3", region_name=self._region)

    async def store(
        self,
        png_bytes: bytes,
        target_site_id: str,
        cycle_id: str,
        height_px: int = 0,
        was_truncated: bool = False,
    ) -> ScreenshotModel:
        """Upload para S3 + persiste metadados no banco.

        Fluxo:
            1. Gera UUID único para o screenshot
            2. Monta chave S3: screenshots/{cycle_id}/{screenshot_id}.png
            3. Faz upload para S3 (multipart se > 5MB)
            4. Somente após upload confirmado: persiste metadados no banco

        Args:
            png_bytes: Conteúdo do screenshot em formato PNG.
            target_site_id: ID do Target Site associado.
            cycle_id: ID do ciclo de monitoramento.
            height_px: Altura do screenshot em pixels.
            was_truncated: Se o screenshot foi truncado (altura > 20000px).

        Returns:
            ScreenshotModel persistido com todos os metadados.

        Raises:
            ClientError: Se o upload para S3 falhar após todas tentativas.
        """
        screenshot_id = str(uuid.uuid4())
        s3_key = f"screenshots/{cycle_id}/{screenshot_id}.png"

        # Upload para S3 com retry (3 tentativas, backoff 1s, 2s, 4s)
        # Metadados NÃO são persistidos se o upload falhar
        if len(png_bytes) > self._multipart_threshold:
            self._upload_multipart(s3_key, png_bytes)
        else:
            self._upload_simple(s3_key, png_bytes)

        logger.info(
            "Screenshot uploaded para S3: %s (%d bytes, %s)",
            s3_key,
            len(png_bytes),
            "multipart" if len(png_bytes) > self._multipart_threshold else "simples",
        )

        # Somente após upload confirmado: persiste metadados no banco
        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at = now + timedelta(days=self._retention_days)

        async with get_session() as session:
            screenshot_model = ScreenshotModel(
                id=screenshot_id,
                target_site_id=target_site_id,
                monitoring_cycle_id=cycle_id,
                s3_key=s3_key,
                captured_at=now,
                height_px=height_px,
                was_truncated=was_truncated,
                expires_at=expires_at,
            )
            session.add(screenshot_model)
            await session.flush()

        logger.info(
            "Screenshot registrado no banco: id=%s, target_site=%s, expires_at=%s",
            screenshot_id,
            target_site_id,
            expires_at.isoformat(),
        )
        return screenshot_model

    async def get_presigned_url(
        self,
        screenshot_id: str,
        expires_in: int = 3600,
    ) -> str:
        """Gera URL pré-assinada com validade configurável (padrão 1 hora).

        Verifica existência no banco antes de gerar URL.
        Verifica existência no S3 antes de gerar URL.

        Args:
            screenshot_id: ID único do screenshot.
            expires_in: Validade da URL em segundos (padrão: 3600 = 1 hora).

        Returns:
            URL pré-assinada para download direto do screenshot.

        Raises:
            ScreenshotNotFoundError: Se screenshot não existe no banco ou no S3.
        """
        # Consulta banco para obter s3_key
        async with get_session() as session:
            stmt = select(ScreenshotModel).where(
                ScreenshotModel.id == screenshot_id
            )
            result = await session.execute(stmt)
            screenshot = result.scalar_one_or_none()

        if screenshot is None:
            raise ScreenshotNotFoundError(
                f"Screenshot não encontrado no banco: id={screenshot_id}"
            )

        s3_key = screenshot.s3_key

        # Verifica existência no S3
        try:
            self._s3_client.head_object(
                Bucket=self._bucket,
                Key=s3_key,
            )
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("NoSuchKey", "404"):
                raise ScreenshotNotFoundError(
                    f"Screenshot não encontrado no S3: key={s3_key}"
                ) from exc
            raise

        # Gera URL pré-assinada
        url = self._s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": s3_key},
            ExpiresIn=expires_in,
        )

        logger.debug(
            "URL pré-assinada gerada: screenshot_id=%s, expires_in=%ds",
            screenshot_id,
            expires_in,
        )
        return url

    async def retrieve(self, screenshot_id: str) -> bytes:
        """Download do S3 por screenshot_id.

        Consulta o banco para obter s3_key e faz download do S3.

        Args:
            screenshot_id: ID único do screenshot.

        Returns:
            Bytes do conteúdo PNG do screenshot.

        Raises:
            ScreenshotNotFoundError: Se screenshot não existe no banco ou no S3.
        """
        # Consulta banco para obter s3_key
        async with get_session() as session:
            stmt = select(ScreenshotModel).where(
                ScreenshotModel.id == screenshot_id
            )
            result = await session.execute(stmt)
            screenshot = result.scalar_one_or_none()

        if screenshot is None:
            raise ScreenshotNotFoundError(
                f"Screenshot não encontrado no banco: id={screenshot_id}"
            )

        s3_key = screenshot.s3_key

        # Download do S3
        try:
            response = self._s3_client.get_object(
                Bucket=self._bucket,
                Key=s3_key,
            )
            data = response["Body"].read()
            logger.debug(
                "Screenshot downloaded do S3: key=%s (%d bytes)",
                s3_key,
                len(data),
            )
            return data
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code == "NoSuchKey":
                raise ScreenshotNotFoundError(
                    f"Screenshot não encontrado no S3: key={s3_key}"
                ) from exc
            raise

    async def cleanup_expired(self) -> int:
        """Remove metadados de screenshots expirados do banco.

        Na arquitetura S3, os objetos são removidos automaticamente via
        S3 Lifecycle Rules (90 dias). Este método apenas limpa os
        metadados no banco de dados.

        Returns:
            Número total de registros removidos do banco.
        """
        now = datetime.now(timezone.utc)
        total_removed = 0

        while True:
            removed_in_batch = await self._cleanup_batch(now)
            total_removed += removed_in_batch

            if removed_in_batch < _CLEANUP_BATCH_SIZE:
                break

        if total_removed > 0:
            logger.info(
                "Cleanup de metadados de screenshots: %d removidos",
                total_removed,
            )

        return total_removed

    async def _cleanup_batch(self, now: datetime) -> int:
        """Remove um batch de metadados de screenshots expirados.

        Args:
            now: Datetime UTC atual para comparação com expires_at.

        Returns:
            Número de registros removidos neste batch.
        """
        async with get_session() as session:
            stmt = (
                select(ScreenshotModel)
                .where(ScreenshotModel.expires_at <= now)
                .limit(_CLEANUP_BATCH_SIZE)
            )
            result = await session.execute(stmt)
            expired_screenshots = result.scalars().all()

            if not expired_screenshots:
                return 0

            for screenshot in expired_screenshots:
                await session.delete(screenshot)

            return len(expired_screenshots)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(ClientError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _upload_simple(self, s3_key: str, data: bytes) -> None:
        """Upload simples (PutObject) com retry e exponential backoff.

        Usado para arquivos <= 5MB.
        Tentativas: até 3, com delays de 1s, 2s, 4s entre elas.

        Args:
            s3_key: Chave do objeto no S3.
            data: Bytes do conteúdo a ser enviado.

        Raises:
            ClientError: Se todas tentativas de upload falharem.
        """
        self._s3_client.put_object(
            Bucket=self._bucket,
            Key=s3_key,
            Body=data,
            ContentType="image/png",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(ClientError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _upload_multipart(self, s3_key: str, data: bytes) -> None:
        """Upload multipart para arquivos > 5MB com retry.

        Divide o arquivo em partes de 5MB e faz upload de cada parte.
        Tentativas: até 3, com delays de 1s, 2s, 4s entre elas.

        Args:
            s3_key: Chave do objeto no S3.
            data: Bytes do conteúdo a ser enviado.

        Raises:
            ClientError: Se todas tentativas de upload falharem.
        """
        upload_id = None
        try:
            # Inicia multipart upload
            response = self._s3_client.create_multipart_upload(
                Bucket=self._bucket,
                Key=s3_key,
                ContentType="image/png",
            )
            upload_id = response["UploadId"]

            # Calcula número de partes
            num_parts = math.ceil(len(data) / _MULTIPART_PART_SIZE)
            parts = []

            for part_number in range(1, num_parts + 1):
                start = (part_number - 1) * _MULTIPART_PART_SIZE
                end = min(start + _MULTIPART_PART_SIZE, len(data))
                part_data = data[start:end]

                part_response = self._s3_client.upload_part(
                    Bucket=self._bucket,
                    Key=s3_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=part_data,
                )
                parts.append(
                    {
                        "ETag": part_response["ETag"],
                        "PartNumber": part_number,
                    }
                )

            # Completa multipart upload
            self._s3_client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=s3_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )

            logger.debug(
                "Multipart upload concluído: %s (%d partes)",
                s3_key,
                num_parts,
            )

        except ClientError:
            # Aborta multipart upload em caso de falha
            if upload_id is not None:
                try:
                    self._s3_client.abort_multipart_upload(
                        Bucket=self._bucket,
                        Key=s3_key,
                        UploadId=upload_id,
                    )
                    logger.warning(
                        "Multipart upload abortado: %s (UploadId=%s)",
                        s3_key,
                        upload_id,
                    )
                except ClientError as abort_err:
                    logger.error(
                        "Falha ao abortar multipart upload: %s - %s",
                        s3_key,
                        abort_err,
                    )
            raise
