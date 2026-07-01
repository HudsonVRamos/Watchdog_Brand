"""Registro de ativos de marca (logos e textos).

Gerencia o ciclo de vida de Brand Assets (logotipos e textos de marca),
incluindo validação, deduplicação via content hash, persistência no banco
e armazenamento de arquivos de logo no filesystem.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from brand_watchdog.models.database import get_session
from brand_watchdog.models.dataclasses import BrandAsset
from brand_watchdog.models.entities import BrandAssetModel
from brand_watchdog.utils.hashing import hash_content, hash_text
from brand_watchdog.utils.validators import BrandAssetValidator

logger = logging.getLogger(__name__)


class BrandRegistry:
    """Gerencia registro e consulta de ativos de marca.

    Responsabilidades:
        - Registrar logos (PNG, JPG, SVG) com validação de formato/tamanho
        - Registrar textos de marca com validação de conteúdo
        - Garantir deduplicação via content_hash (SHA-256)
        - Armazenar arquivos de logo no filesystem
        - Remover ativos (banco + arquivo físico)

    Args:
        logo_storage_path: Diretório para armazenamento de arquivos de logo.
    """

    def __init__(self, logo_storage_path: Path) -> None:
        self._logo_storage_path = logo_storage_path
        self._validator = BrandAssetValidator()

    async def register_logo(
        self, image_data: bytes, filename: str
    ) -> BrandAsset:
        """Registra imagem de logotipo como ativo de marca.

        Fluxo:
            1. Valida formato (PNG, JPG, SVG) e tamanho (max 5 MB)
            2. Calcula content_hash (SHA-256)
            3. Verifica duplicata no banco de dados
            4. Salva arquivo no filesystem
            5. Persiste registro no banco de dados

        Args:
            image_data: Bytes do conteúdo da imagem.
            filename: Nome original do arquivo.

        Returns:
            BrandAsset com dados do ativo registrado.

        Raises:
            ValueError: Se formato/tamanho inválido ou asset duplicado.
        """
        # 1. Validar formato e tamanho
        validation = self._validator.validate_image(image_data, filename)
        if not validation.valid:
            raise ValueError(validation.error)

        # 2. Calcular hash do conteúdo
        content_hash = hash_content(image_data)

        # 3. Verificar duplicata no banco
        async with get_session() as session:
            stmt = select(BrandAssetModel).where(
                BrandAssetModel.content_hash == content_hash
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is not None:
                raise ValueError(
                    "Ativo de marca já existe com conteúdo idêntico"
                )

            # 4. Salvar arquivo no filesystem
            self._logo_storage_path.mkdir(parents=True, exist_ok=True)
            file_path = self._logo_storage_path / f"{content_hash}_{filename}"
            file_path.write_bytes(image_data)

            logger.info(
                "Logo salvo em: %s (%d bytes)",
                file_path,
                len(image_data),
            )

            # 5. Persistir no banco de dados
            asset_model = BrandAssetModel(
                asset_type="logo",
                file_path=str(file_path),
                text_value=None,
                content_hash=content_hash,
                original_filename=filename,
                file_size_bytes=len(image_data),
            )
            session.add(asset_model)
            await session.flush()

            # Converter para dataclass de retorno
            brand_asset = BrandAsset(
                id=asset_model.id,
                asset_type=asset_model.asset_type,
                file_path=Path(asset_model.file_path),
                text_value=None,
                content_hash=asset_model.content_hash,
                original_filename=asset_model.original_filename,
                file_size_bytes=asset_model.file_size_bytes,
                created_at=asset_model.created_at,
            )

        logger.info(
            "Logo registrado com sucesso: id=%s, hash=%s",
            brand_asset.id,
            content_hash,
        )
        return brand_asset

    async def register_text(self, text: str) -> BrandAsset:
        """Registra texto como ativo de marca.

        Fluxo:
            1. Valida regras de texto (comprimento, caracteres visíveis)
            2. Calcula content_hash (SHA-256 do texto)
            3. Verifica duplicata no banco de dados
            4. Persiste registro no banco de dados

        Args:
            text: Texto de marca a ser registrado.

        Returns:
            BrandAsset com dados do ativo registrado.

        Raises:
            ValueError: Se texto inválido ou asset duplicado.
        """
        # 1. Validar texto
        validation = self._validator.validate_text(text)
        if not validation.valid:
            raise ValueError(validation.error)

        # 2. Calcular hash do texto
        content_hash = hash_text(text)

        # 3. Verificar duplicata no banco
        async with get_session() as session:
            stmt = select(BrandAssetModel).where(
                BrandAssetModel.content_hash == content_hash
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is not None:
                raise ValueError(
                    "Ativo de marca já existe com conteúdo idêntico"
                )

            # 4. Persistir no banco de dados
            asset_model = BrandAssetModel(
                asset_type="text",
                file_path=None,
                text_value=text,
                content_hash=content_hash,
                original_filename=None,
                file_size_bytes=None,
            )
            session.add(asset_model)
            await session.flush()

            # Converter para dataclass de retorno
            brand_asset = BrandAsset(
                id=asset_model.id,
                asset_type=asset_model.asset_type,
                file_path=None,
                text_value=asset_model.text_value,
                content_hash=asset_model.content_hash,
                original_filename=None,
                file_size_bytes=None,
                created_at=asset_model.created_at,
            )

        logger.info(
            "Texto de marca registrado com sucesso: id=%s, hash=%s",
            brand_asset.id,
            content_hash,
        )
        return brand_asset

    async def get_all_assets(self) -> list[BrandAsset]:
        """Retorna todos os ativos de marca registrados.

        Returns:
            Lista de BrandAsset com todos os ativos do registro.
        """
        async with get_session() as session:
            stmt = select(BrandAssetModel)
            result = await session.execute(stmt)
            models = result.scalars().all()

            assets = [
                BrandAsset(
                    id=model.id,
                    asset_type=model.asset_type,
                    file_path=(
                        Path(model.file_path) if model.file_path else None
                    ),
                    text_value=model.text_value,
                    content_hash=model.content_hash,
                    original_filename=model.original_filename,
                    file_size_bytes=model.file_size_bytes,
                    created_at=model.created_at,
                )
                for model in models
            ]

        return assets

    async def remove_asset(self, asset_id: str) -> bool:
        """Remove um ativo de marca pelo ID.

        Remove o registro do banco de dados e, se for um logo,
        também remove o arquivo físico do filesystem.

        Args:
            asset_id: Identificador único do ativo a remover.

        Returns:
            True se o ativo foi removido, False se não encontrado.
        """
        async with get_session() as session:
            stmt = select(BrandAssetModel).where(
                BrandAssetModel.id == asset_id
            )
            result = await session.execute(stmt)
            asset_model = result.scalar_one_or_none()

            if asset_model is None:
                return False

            # Se for logo, remove o arquivo físico
            if asset_model.asset_type == "logo" and asset_model.file_path:
                file_path = Path(asset_model.file_path)
                if file_path.exists():
                    file_path.unlink()
                    logger.info("Arquivo de logo removido: %s", file_path)

            # Remove do banco de dados
            await session.delete(asset_model)

        logger.info("Ativo de marca removido: id=%s", asset_id)
        return True
