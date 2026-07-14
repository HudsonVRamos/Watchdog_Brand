"""Property test para corretude do redimensionamento de imagem.

# Feature: architecture-evolution, Property 13: Corretude do Redimensionamento de Imagem

**Validates: Requirements 8.1, 8.6**

Para qualquer imagem de referência com dimensões (W, H), o redimensionamento
SHALL produzir uma imagem em formato JPEG (quality=85) onde
max(W', H') ≤ 1568px e a proporção original é preservada
(W'/H' ≈ W/H com tolerância de ±1px por arredondamento).
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from PIL import Image

from brand_watchdog.analyzer.reference_image_cache import ReferenceImageCache


_PBT_SETTINGS = settings(max_examples=30, deadline=None)

# Constantes do requisito
_MAX_SIZE_PX = 1568
_JPEG_QUALITY = 85


@st.composite
def image_dimensions(draw: st.DrawFn) -> tuple[int, int]:
    """Gera dimensões aleatórias de imagem (1-5000 x 1-5000).

    Filtra combinações que resultariam em dimensão 0 após
    redimensionamento (ex: 1x5000 → round(1 * 1568/5000) = 0).
    """
    width = draw(st.integers(min_value=1, max_value=5000))
    height = draw(st.integers(min_value=1, max_value=5000))

    # Garantir que o redimensionamento não produz dimensão 0px
    max_dim = max(width, height)
    if max_dim > _MAX_SIZE_PX:
        scale = _MAX_SIZE_PX / max_dim
        new_w = round(width * scale)
        new_h = round(height * scale)
        # Filtrar casos onde arredondamento levaria a 0
        assume(new_w >= 1 and new_h >= 1)

    return (width, height)


class TestImageResizeCorrectness:
    """Property 13: Corretude do Redimensionamento de Imagem.

    **Validates: Requirements 8.1, 8.6**
    """

    @_PBT_SETTINGS
    @given(dims=image_dimensions())
    def test_resized_image_max_dimension_within_limit(
        self, dims: tuple[int, int]
    ) -> None:
        """max(W', H') ≤ 1568px após redimensionamento."""
        width, height = dims

        cache = ReferenceImageCache(
            max_size_px=_MAX_SIZE_PX, jpeg_quality=_JPEG_QUALITY
        )

        # Criar imagem PIL em arquivo temporário
        img = Image.new("RGB", (width, height), color=(128, 64, 200))

        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False
        ) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = Path(tmp.name)

        try:
            result = cache.load_and_resize(tmp_path)

            assert result is not None, (
                f"load_and_resize retornou None para imagem "
                f"válida {width}x{height}"
            )

            # Abrir resultado JPEG e verificar dimensões
            resized_img = Image.open(io.BytesIO(result))
            new_width, new_height = resized_img.size

            max_dim = max(new_width, new_height)
            assert max_dim <= _MAX_SIZE_PX, (
                f"Dimensão máxima {max_dim}px excede limite "
                f"de {_MAX_SIZE_PX}px. "
                f"Original: {width}x{height}, "
                f"Redimensionada: {new_width}x{new_height}"
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    @_PBT_SETTINGS
    @given(dims=image_dimensions())
    def test_resized_image_preserves_aspect_ratio(
        self, dims: tuple[int, int]
    ) -> None:
        """Proporção original preservada (tolerância ±1px por arredondamento)."""
        width, height = dims

        cache = ReferenceImageCache(
            max_size_px=_MAX_SIZE_PX, jpeg_quality=_JPEG_QUALITY
        )

        # Criar imagem PIL em arquivo temporário
        img = Image.new("RGB", (width, height), color=(50, 150, 100))

        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False
        ) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = Path(tmp.name)

        try:
            result = cache.load_and_resize(tmp_path)

            assert result is not None, (
                f"load_and_resize retornou None para imagem "
                f"válida {width}x{height}"
            )

            resized_img = Image.open(io.BytesIO(result))
            new_width, new_height = resized_img.size

            # Para imagens que não precisam de redimensionamento
            # (max(W,H) <= 1568), as dimensões devem ser iguais
            if max(width, height) <= _MAX_SIZE_PX:
                assert new_width == width, (
                    f"Imagem pequena não deveria mudar width: "
                    f"{new_width} != {width}"
                )
                assert new_height == height, (
                    f"Imagem pequena não deveria mudar height: "
                    f"{new_height} != {height}"
                )
            else:
                # Verificar proporção com tolerância de ±1px
                scale = _MAX_SIZE_PX / max(width, height)
                expected_width = round(width * scale)
                expected_height = round(height * scale)

                assert abs(new_width - expected_width) <= 1, (
                    f"Width fora da tolerância: "
                    f"esperado ~{expected_width}, obtido {new_width}. "
                    f"Original: {width}x{height}, scale={scale:.6f}"
                )
                assert abs(new_height - expected_height) <= 1, (
                    f"Height fora da tolerância: "
                    f"esperado ~{expected_height}, obtido {new_height}. "
                    f"Original: {width}x{height}, scale={scale:.6f}"
                )
        finally:
            tmp_path.unlink(missing_ok=True)

    @_PBT_SETTINGS
    @given(dims=image_dimensions())
    def test_resized_image_is_valid_jpeg(
        self, dims: tuple[int, int]
    ) -> None:
        """Resultado é um JPEG válido (formato conforme requisito 8.6)."""
        width, height = dims

        cache = ReferenceImageCache(
            max_size_px=_MAX_SIZE_PX, jpeg_quality=_JPEG_QUALITY
        )

        # Criar imagem PIL em arquivo temporário
        img = Image.new("RGB", (width, height), color=(200, 100, 50))

        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False
        ) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = Path(tmp.name)

        try:
            result = cache.load_and_resize(tmp_path)

            assert result is not None, (
                f"load_and_resize retornou None para imagem "
                f"válida {width}x{height}"
            )

            # Verificar que o resultado é JPEG válido
            resized_img = Image.open(io.BytesIO(result))
            assert resized_img.format == "JPEG", (
                f"Formato esperado JPEG, obtido {resized_img.format}. "
                f"Original: {width}x{height}"
            )
        finally:
            tmp_path.unlink(missing_ok=True)
