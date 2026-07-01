"""Armazenamento de screenshots no filesystem local.

Gerencia o ciclo de vida de screenshots capturados:
- Armazenamento de arquivos PNG com retry e exponential backoff
- Associação com Target_Site e ciclo de monitoramento
- Cálculo de expiração baseado em configuração
- Limpeza automática de screenshots expirados
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import get_session
from brand_watchdog.models.entities import ScreenshotModel

logger = logging.getLogger(__name__)

# Tamanho do batch para operações de cleanup
_CLEANUP_BATCH_SIZE = 100


class ScreenshotStore:
    """Gerencia armazenamento e recuperação de screenshots.

    Responsabilidades:
        - Armazenar screenshots como PNG no filesystem com retry
        - Associar screenshots a Target Sites e ciclos de monitoramento
        - Calcular e aplicar período de expiração configurável
        - Remover screenshots expirados (arquivos + registros do banco)

    Args:
        config: Configuração de storage com path base e retention days.
    """

    def __init__(self, config: StorageConfig) -> None:
        self._base_path = config.screenshot_base_path
        self._retention_days = config.screenshot_retention_days

    async def store(
        self,
        png_bytes: bytes,
        target_site_id: str,
        cycle_id: str,
        height_px: int = 0,
        was_truncated: bool = False,
    ) -> ScreenshotModel:
        """Armazena screenshot no filesystem e persiste metadados no banco.

        Fluxo:
            1. Gera UUID único para o screenshot
            2. Cria diretório do ciclo se necessário
            3. Escreve bytes PNG no filesystem (com retry)
            4. Calcula expires_at com base em retention_days
            5. Persiste ScreenshotModel no banco de dados

        Args:
            png_bytes: Conteúdo do screenshot em formato PNG.
            target_site_id: ID do Target Site associado.
            cycle_id: ID do ciclo de monitoramento.
            height_px: Altura do screenshot em pixels.
            was_truncated: Se o screenshot foi truncado (altura > 20000px).

        Returns:
            ScreenshotModel persistido com todos os metadados.

        Raises:
            IOError: Se a escrita no filesystem falhar após todas as tentativas.
        """
        screenshot_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at = now + timedelta(days=self._retention_days)

        # Cria diretório do ciclo
        cycle_dir = self._base_path / cycle_id
        cycle_dir.mkdir(parents=True, exist_ok=True)

        # Path final do arquivo
        file_path = cycle_dir / f"{screenshot_id}.png"

        # Escrita com retry (exponential backoff: 1s, 2s, 4s)
        self._write_file(file_path, png_bytes)

        logger.info(
            "Screenshot salvo: %s (%d bytes)",
            file_path,
            len(png_bytes),
        )

        # Persiste metadados no banco de dados
        async with get_session() as session:
            screenshot_model = ScreenshotModel(
                id=screenshot_id,
                target_site_id=target_site_id,
                monitoring_cycle_id=cycle_id,
                file_path=str(file_path),
                captured_at=now,
                height_px=height_px,
                was_truncated=was_truncated,
                expires_at=expires_at,
            )
            session.add(screenshot_model)
            await session.flush()

        logger.info(
            "Screenshot registrado: id=%s, target_site=%s, expires_at=%s",
            screenshot_id,
            target_site_id,
            expires_at.isoformat(),
        )
        return screenshot_model

    async def retrieve(self, screenshot_id: str) -> bytes:
        """Recupera conteúdo de um screenshot pelo ID.

        Consulta o banco para obter o file_path e lê o arquivo do filesystem.

        Args:
            screenshot_id: ID único do screenshot.

        Returns:
            Bytes do conteúdo PNG do screenshot.

        Raises:
            FileNotFoundError: Se o screenshot não existe no banco ou no filesystem.
        """
        async with get_session() as session:
            stmt = select(ScreenshotModel).where(
                ScreenshotModel.id == screenshot_id
            )
            result = await session.execute(stmt)
            screenshot = result.scalar_one_or_none()

        if screenshot is None:
            raise FileNotFoundError(
                f"Screenshot não encontrado no banco: id={screenshot_id}"
            )

        file_path = Path(screenshot.file_path)
        if not file_path.exists():
            raise FileNotFoundError(
                f"Arquivo de screenshot não encontrado: {file_path}"
            )

        return file_path.read_bytes()

    async def cleanup_expired(self) -> int:
        """Remove screenshots expirados (arquivos + registros do banco).

        Processa em batches de 100 para não sobrecarregar o banco.
        Remove o arquivo físico e depois o registro do banco de dados.

        Returns:
            Número total de screenshots removidos.
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
                "Cleanup de screenshots concluído: %d removidos",
                total_removed,
            )

        return total_removed

    async def _cleanup_batch(self, now: datetime) -> int:
        """Remove um batch de screenshots expirados.

        Args:
            now: Datetime UTC atual para comparação com expires_at.

        Returns:
            Número de screenshots removidos neste batch.
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
                # Remove arquivo físico
                file_path = Path(screenshot.file_path)
                if file_path.exists():
                    try:
                        file_path.unlink()
                        logger.debug(
                            "Arquivo removido: %s", file_path
                        )
                    except OSError as e:
                        logger.warning(
                            "Falha ao remover arquivo %s: %s",
                            file_path,
                            e,
                        )

                # Remove registro do banco
                await session.delete(screenshot)

            return len(expired_screenshots)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(IOError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _write_file(self, file_path: Path, data: bytes) -> None:
        """Escreve bytes no filesystem com retry e exponential backoff.

        Tentativas: até 3, com delays de 1s, 2s, 4s entre elas.

        Args:
            file_path: Caminho completo do arquivo a ser escrito.
            data: Bytes do conteúdo a ser gravado.

        Raises:
            IOError: Se todas as tentativas de escrita falharem.
        """
        file_path.write_bytes(data)
