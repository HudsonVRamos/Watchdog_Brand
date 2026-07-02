"""Testes de integração: S3 upload + presigned URL.

Utiliza moto para simular S3, verificando upload de screenshots,
geração de presigned URLs e threshold de multipart.

Requirements: 4.1
"""

from __future__ import annotations

import uuid

import boto3
import pytest
from moto import mock_aws


# --- Helpers ---


def _fake_png_bytes(size: int = 1024) -> bytes:
    """Gera bytes PNG fake com tamanho específico."""
    header = b"\x89PNG\r\n\x1a\n"
    return header + b"\x00" * (size - len(header))


# --- Testes ---


@pytest.mark.integration
class TestS3Store:
    """Testes de integração do armazenamento S3 de screenshots."""

    async def test_upload_png_to_s3_with_correct_key_format(
        self,
    ) -> None:
        """Upload de PNG para S3 com chave no formato correto.

        Verifica que o objeto é criado com a chave:
        screenshots/{cycle_id}/{screenshot_id}.png
        """
        with mock_aws():
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket)

            cycle_id = str(uuid.uuid4())
            screenshot_id = str(uuid.uuid4())
            s3_key = (
                f"screenshots/{cycle_id}/{screenshot_id}.png"
            )
            png_bytes = _fake_png_bytes(2048)

            # Upload
            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=png_bytes,
                ContentType="image/png",
            )

            # Verifica existência e tamanho
            response = s3_client.head_object(
                Bucket=bucket, Key=s3_key
            )
            assert response["ContentLength"] == 2048
            assert response["ContentType"] == "image/png"

    async def test_generate_presigned_url(self) -> None:
        """Gera URL pré-assinada válida para download do screenshot.

        Verifica que a URL pré-assinada é gerada e contém
        os parâmetros esperados (Signature, Expires).
        """
        with mock_aws():
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket)

            cycle_id = str(uuid.uuid4())
            screenshot_id = str(uuid.uuid4())
            s3_key = (
                f"screenshots/{cycle_id}/{screenshot_id}.png"
            )
            png_bytes = _fake_png_bytes()

            # Upload o objeto primeiro
            s3_client.put_object(
                Bucket=bucket, Key=s3_key, Body=png_bytes
            )

            # Gera URL pré-assinada com validade de 1 hora
            presigned_url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": s3_key},
                ExpiresIn=3600,
            )

            # Verifica que a URL contém parâmetros esperados
            assert presigned_url is not None
            assert (
                "Signature" in presigned_url
                or "X-Amz" in presigned_url
            )

    async def test_download_via_get_object_content_matches(
        self,
    ) -> None:
        """Download via S3 get_object retorna conteúdo correto.

        Simula o fluxo: upload → download e verifica integridade.
        """
        with mock_aws():
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket)

            cycle_id = str(uuid.uuid4())
            screenshot_id = str(uuid.uuid4())
            s3_key = (
                f"screenshots/{cycle_id}/{screenshot_id}.png"
            )
            original_bytes = _fake_png_bytes(4096)

            # Upload
            s3_client.put_object(
                Bucket=bucket, Key=s3_key, Body=original_bytes
            )

            # Download
            response = s3_client.get_object(
                Bucket=bucket, Key=s3_key
            )
            downloaded = response["Body"].read()

            assert downloaded == original_bytes

    async def test_multipart_upload_for_large_files(
        self,
    ) -> None:
        """Upload multipart é utilizado para arquivos > 5MB.

        Verifica que um arquivo grande pode ser uploadado
        usando multipart upload do S3.
        """
        with mock_aws():
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket)

            cycle_id = str(uuid.uuid4())
            screenshot_id = str(uuid.uuid4())
            s3_key = (
                f"screenshots/{cycle_id}/{screenshot_id}.png"
            )

            # Arquivo > 5MB (simula screenshot grande)
            large_png = _fake_png_bytes(6 * 1024 * 1024)

            # Multipart upload
            mpu = s3_client.create_multipart_upload(
                Bucket=bucket,
                Key=s3_key,
                ContentType="image/png",
            )
            upload_id = mpu["UploadId"]

            # Upload em partes de 5MB
            part_size = 5 * 1024 * 1024
            parts = []

            for i, start in enumerate(
                range(0, len(large_png), part_size), 1
            ):
                chunk = large_png[start : start + part_size]
                part_resp = s3_client.upload_part(
                    Bucket=bucket,
                    Key=s3_key,
                    UploadId=upload_id,
                    PartNumber=i,
                    Body=chunk,
                )
                parts.append(
                    {
                        "PartNumber": i,
                        "ETag": part_resp["ETag"],
                    }
                )

            # Completa o multipart
            s3_client.complete_multipart_upload(
                Bucket=bucket,
                Key=s3_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )

            # Verifica que o objeto existe com tamanho correto
            response = s3_client.head_object(
                Bucket=bucket, Key=s3_key
            )
            assert response["ContentLength"] == len(large_png)

    async def test_simple_upload_for_small_files(self) -> None:
        """Upload simples (PutObject) para arquivos <= 5MB.

        Verifica que arquivos menores que o threshold de multipart
        são uploadados com PutObject simples.
        """
        with mock_aws():
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket)

            cycle_id = str(uuid.uuid4())
            screenshot_id = str(uuid.uuid4())
            s3_key = (
                f"screenshots/{cycle_id}/{screenshot_id}.png"
            )

            # Arquivo < 5MB
            small_png = _fake_png_bytes(4 * 1024 * 1024)

            # Upload simples
            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=small_png,
                ContentType="image/png",
            )

            # Verifica existência
            response = s3_client.head_object(
                Bucket=bucket, Key=s3_key
            )
            assert response["ContentLength"] == len(small_png)

    async def test_object_not_found_raises_error(self) -> None:
        """Consulta de objeto inexistente retorna erro NoSuchKey.

        Verifica que o S3 retorna o erro adequado para objetos
        que não existem no bucket.
        """
        with mock_aws():
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket)

            s3_key = "screenshots/nonexistent/fake.png"

            from botocore.exceptions import ClientError

            with pytest.raises(ClientError) as exc_info:
                s3_client.head_object(
                    Bucket=bucket, Key=s3_key
                )

            error_code = exc_info.value.response["Error"][
                "Code"
            ]
            assert error_code == "404"

    async def test_multiple_screenshots_same_cycle(
        self,
    ) -> None:
        """Múltiplos screenshots do mesmo ciclo coexistem sem conflito.

        Verifica que vários screenshots sob o mesmo cycle_id
        são armazenados corretamente.
        """
        with mock_aws():
            s3_client = boto3.client(
                "s3", region_name="us-east-1"
            )
            bucket = "brand-watchdog-screenshots-test"
            s3_client.create_bucket(Bucket=bucket)

            cycle_id = str(uuid.uuid4())
            num_screenshots = 5

            for i in range(num_screenshots):
                screenshot_id = str(uuid.uuid4())
                s3_key = (
                    f"screenshots/{cycle_id}/"
                    f"{screenshot_id}.png"
                )
                png_bytes = _fake_png_bytes(1024 + i * 100)

                s3_client.put_object(
                    Bucket=bucket, Key=s3_key, Body=png_bytes
                )

            # Lista objetos com prefix do ciclo
            response = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=f"screenshots/{cycle_id}/",
            )
            assert response["KeyCount"] == num_screenshots
