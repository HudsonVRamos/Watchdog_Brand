"""Testes unitários para ReferenceImageCache.

Valida o comportamento de:
- load_and_resize: carregamento, redimensionamento e conversão JPEG
- get_cached_images: retorno de imagens por brand
- clear: limpeza do cache
- Tratamento de imagens corrompidas/não suportadas
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from PIL import Image

from brand_watchdog.analyzer.reference_image_cache import (
    ReferenceImageCache,
)


@pytest.fixture
def cache() -> ReferenceImageCache:
    """Cache com configuração padrão."""
    return ReferenceImageCache(max_size_px=1568, jpeg_quality=85)


@pytest.fixture
def small_png(tmp_path: Path) -> Path:
    """Cria uma imagem PNG pequena (100x100) no diretório temporário."""
    img = Image.new("RGB", (100, 100), color="red")
    path = tmp_path / "small.png"
    img.save(path, format="PNG")
    return path


@pytest.fixture
def large_png(tmp_path: Path) -> Path:
    """Cria uma imagem PNG grande (3000x2000) no diretório temporário."""
    img = Image.new("RGB", (3000, 2000), color="blue")
    path = tmp_path / "large.png"
    img.save(path, format="PNG")
    return path


@pytest.fixture
def rgba_png(tmp_path: Path) -> Path:
    """Cria uma imagem RGBA (com transparência) no diretório temporário."""
    img = Image.new("RGBA", (200, 150), color=(0, 255, 0, 128))
    path = tmp_path / "rgba.png"
    img.save(path, format="PNG")
    return path


@pytest.fixture
def corrupted_file(tmp_path: Path) -> Path:
    """Cria um arquivo com conteúdo inválido para simular imagem corrompida."""
    path = tmp_path / "corrupted.png"
    path.write_bytes(b"este nao e um arquivo de imagem valido")
    return path


class TestLoadAndResize:
    """Testes para o método load_and_resize."""

    def test_imagem_pequena_nao_redimensionada(
        self, cache: ReferenceImageCache, small_png: Path
    ) -> None:
        """Imagens menores que max_size_px não devem ser redimensionadas."""
        result = cache.load_and_resize(small_png)

        assert result is not None
        # Verificar que é um JPEG válido
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"
        # Dimensões preservadas (100x100)
        assert img.size == (100, 100)

    def test_imagem_grande_redimensionada(
        self, cache: ReferenceImageCache, large_png: Path
    ) -> None:
        """Imagens maiores que max_size_px devem ser redimensionadas."""
        result = cache.load_and_resize(large_png)

        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"
        # O lado maior deve ser <= 1568
        width, height = img.size
        assert max(width, height) <= 1568
        # Proporção preservada (3000x2000 -> ~1568x1045)
        assert width == 1568
        assert height == round(2000 * (1568 / 3000))

    def test_proporcao_preservada_vertical(
        self, tmp_path: Path
    ) -> None:
        """Imagem vertical deve preservar proporção ao redimensionar."""
        img = Image.new("RGB", (1000, 3000), color="green")
        path = tmp_path / "vertical.png"
        img.save(path, format="PNG")

        cache = ReferenceImageCache(max_size_px=1568)
        result = cache.load_and_resize(path)

        assert result is not None
        resized = Image.open(io.BytesIO(result))
        width, height = resized.size
        assert max(width, height) <= 1568
        assert height == 1568
        assert width == round(1000 * (1568 / 3000))

    def test_conversao_rgba_para_jpeg(
        self, cache: ReferenceImageCache, rgba_png: Path
    ) -> None:
        """Imagens RGBA devem ser convertidas para RGB antes de salvar JPEG."""
        result = cache.load_and_resize(rgba_png)

        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_imagem_corrompida_retorna_none(
        self, cache: ReferenceImageCache, corrupted_file: Path
    ) -> None:
        """Imagem corrompida deve retornar None sem lançar exceção."""
        result = cache.load_and_resize(corrupted_file)
        assert result is None

    def test_arquivo_inexistente_retorna_none(
        self, cache: ReferenceImageCache, tmp_path: Path
    ) -> None:
        """Arquivo inexistente deve retornar None sem lançar exceção."""
        path = tmp_path / "nao_existe.png"
        result = cache.load_and_resize(path)
        assert result is None

    def test_log_info_tamanho_imagem(
        self,
        cache: ReferenceImageCache,
        small_png: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Deve registrar em log INFO o tamanho original e redimensionado."""
        with caplog.at_level(logging.INFO):
            cache.load_and_resize(small_png)

        assert any("small.png" in record.message for record in caplog.records)
        assert any("B ->" in record.message for record in caplog.records)

    def test_log_warning_imagem_corrompida(
        self,
        cache: ReferenceImageCache,
        corrupted_file: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Deve registrar warning para imagem corrompida."""
        with caplog.at_level(logging.WARNING):
            cache.load_and_resize(corrupted_file)

        assert any(
            "não pôde ser processada" in record.message
            for record in caplog.records
        )

    def test_qualidade_jpeg_configuravel(self, tmp_path: Path) -> None:
        """Qualidade JPEG deve ser configurável via construtor."""
        img = Image.new("RGB", (500, 500), color="white")
        path = tmp_path / "quality_test.png"
        img.save(path, format="PNG")

        cache_low = ReferenceImageCache(jpeg_quality=10)
        cache_high = ReferenceImageCache(jpeg_quality=95)

        result_low = cache_low.load_and_resize(path)
        result_high = cache_high.load_and_resize(path)

        assert result_low is not None
        assert result_high is not None
        # Qualidade menor produz arquivo menor
        assert len(result_low) < len(result_high)

    def test_max_size_px_configuravel(self, tmp_path: Path) -> None:
        """Tamanho máximo deve ser configurável via construtor."""
        img = Image.new("RGB", (2000, 1000), color="purple")
        path = tmp_path / "custom_size.png"
        img.save(path, format="PNG")

        cache = ReferenceImageCache(max_size_px=800)
        result = cache.load_and_resize(path)

        assert result is not None
        resized = Image.open(io.BytesIO(result))
        assert max(resized.size) <= 800


class TestGetCachedImages:
    """Testes para o método get_cached_images."""

    def test_brand_sem_cache_retorna_lista_vazia(
        self, cache: ReferenceImageCache
    ) -> None:
        """Brand sem imagens em cache retorna lista vazia."""
        result = cache.get_cached_images("sky_plus")
        assert result == []

    def test_retorna_imagens_do_brand(
        self, cache: ReferenceImageCache
    ) -> None:
        """Deve retornar imagens cacheadas para o brand correto."""
        cache.cache_image("sky_plus", b"img1", "logo")
        cache.cache_image("sky_plus", b"img2", "kv")
        cache.cache_image("dgo", b"img3", "logo_dgo")

        sky_images = cache.get_cached_images("sky_plus")
        assert len(sky_images) == 2
        assert sky_images[0] == (b"img1", "logo")
        assert sky_images[1] == (b"img2", "kv")

        dgo_images = cache.get_cached_images("dgo")
        assert len(dgo_images) == 1
        assert dgo_images[0] == (b"img3", "logo_dgo")


class TestClear:
    """Testes para o método clear."""

    def test_limpa_todas_as_entradas(
        self, cache: ReferenceImageCache
    ) -> None:
        """clear() deve remover todas as imagens de todos os brands."""
        cache.cache_image("sky_plus", b"img1", "logo")
        cache.cache_image("dgo", b"img2", "logo_dgo")

        cache.clear()

        assert cache.get_cached_images("sky_plus") == []
        assert cache.get_cached_images("dgo") == []


class TestIntegracaoLoadAndCache:
    """Testes de integração entre load_and_resize e cache_image."""

    def test_fluxo_completo_load_cache_get(
        self, cache: ReferenceImageCache, small_png: Path
    ) -> None:
        """Fluxo completo: load -> cache -> get."""
        result = cache.load_and_resize(small_png)
        assert result is not None

        cache.cache_image("sky_plus", result, "referencia")

        cached = cache.get_cached_images("sky_plus")
        assert len(cached) == 1
        assert cached[0][0] == result
        assert cached[0][1] == "referencia"

    def test_imagem_corrompida_nao_quebra_processamento(
        self,
        cache: ReferenceImageCache,
        corrupted_file: Path,
        small_png: Path,
    ) -> None:
        """Imagem corrompida não impede processamento de outras imagens."""
        result_corrupted = cache.load_and_resize(corrupted_file)
        result_valid = cache.load_and_resize(small_png)

        assert result_corrupted is None
        assert result_valid is not None
