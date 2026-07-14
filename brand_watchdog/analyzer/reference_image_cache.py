"""Cache em memória de imagens de referência redimensionadas.

Carrega imagens de referência, redimensiona para resolução máxima
de 1568px no lado maior (preservando proporção), converte para
JPEG quality 85 e mantém em cache por brand durante o ciclo.

Requisitos cobertos: 8.1, 8.2, 8.5, 8.6, 8.7
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


class ReferenceImageCache:
    """Cache em memória de imagens de referência redimensionadas.

    Carrega imagens do disco, redimensiona para max_size_px no lado
    maior (preservando proporção original), converte para JPEG com
    qualidade configurável e armazena em memória por brand.

    O cache deve ser limpo ao final de cada ciclo de monitoramento
    via o método clear().
    """

    def __init__(
        self, max_size_px: int = 1568, jpeg_quality: int = 85
    ) -> None:
        """Inicializa o cache de imagens de referência.

        Args:
            max_size_px: Tamanho máximo em pixels no lado maior
                da imagem redimensionada. Padrão: 1568.
            jpeg_quality: Qualidade JPEG para conversão (0-100).
                Padrão: 85.
        """
        self._max_size_px = max_size_px
        self._jpeg_quality = jpeg_quality
        self._cache: dict[str, list[tuple[bytes, str]]] = {}

    def load_and_resize(self, image_path: Path) -> bytes | None:
        """Carrega imagem, redimensiona e converte para JPEG.

        Redimensiona a imagem para que max(width, height) <= max_size_px,
        preservando a proporção original. Usa interpolação LANCZOS para
        downscale. Converte para JPEG com qualidade configurada.

        Args:
            image_path: Caminho para o arquivo de imagem.

        Returns:
            Bytes da imagem redimensionada em formato JPEG, ou None
            se a imagem está corrompida ou em formato não suportado.
        """
        filename = image_path.name

        try:
            with Image.open(image_path) as img:
                original_size = image_path.stat().st_size
                original_dimensions = img.size

                # Converter para RGB se necessário (RGBA, P, etc.)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                elif img.mode == "L":
                    img = img.convert("RGB")

                # Redimensionar se necessário
                width, height = img.size
                max_dim = max(width, height)

                if max_dim > self._max_size_px:
                    scale_factor = self._max_size_px / max_dim
                    new_width = round(width * scale_factor)
                    new_height = round(height * scale_factor)
                    img = img.resize(
                        (new_width, new_height), Image.LANCZOS
                    )

                # Converter para JPEG em memória
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=self._jpeg_quality)
                jpeg_bytes = buffer.getvalue()

                resized_size = len(jpeg_bytes)
                new_dimensions = img.size

                logger.info(
                    "Imagem %s: %dB -> %dB "
                    "(original: %dx%d, redimensionada: %dx%d)",
                    filename,
                    original_size,
                    resized_size,
                    original_dimensions[0],
                    original_dimensions[1],
                    new_dimensions[0],
                    new_dimensions[1],
                )

                return jpeg_bytes

        except UnidentifiedImageError as e:
            logger.warning(
                "Imagem %s não pôde ser processada: %s",
                filename,
                e,
            )
            return None
        except OSError as e:
            logger.warning(
                "Imagem %s não pôde ser processada: %s",
                filename,
                e,
            )
            return None
        except Exception as e:
            logger.warning(
                "Imagem %s não pôde ser processada: %s",
                filename,
                e,
            )
            return None

    def cache_image(
        self, brand: str, image_bytes: bytes, label: str
    ) -> None:
        """Adiciona uma imagem processada ao cache de um brand.

        Args:
            brand: Identificador do brand ("sky_plus" ou "dgo").
            image_bytes: Bytes da imagem já processada (JPEG).
            label: Rótulo/identificador da imagem.
        """
        if brand not in self._cache:
            self._cache[brand] = []
        self._cache[brand].append((image_bytes, label))

    def get_cached_images(self, brand: str) -> list[tuple[bytes, str]]:
        """Retorna imagens já processadas para o brand (do cache em memória).

        Args:
            brand: Identificador do brand ("sky_plus" ou "dgo").

        Returns:
            Lista de tuplas (image_bytes, label) para o brand.
            Lista vazia se o brand não possui imagens em cache.
        """
        return self._cache.get(brand, [])

    def clear(self) -> None:
        """Limpa cache ao final do ciclo."""
        self._cache.clear()
