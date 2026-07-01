"""Testes unitários para o BrandRegistry.

Valida registro de logos e textos de marca, deduplicação,
validação de entrada e remoção de ativos.
"""

import pytest
from pathlib import Path

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    init_db,
    setup_database,
)
from brand_watchdog.registry.brand_registry import BrandRegistry


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

# JPG mínimo válido (header)
VALID_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 100

# SVG mínimo válido
VALID_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'


@pytest.fixture(autouse=True)
async def setup_test_db():
    """Configura banco in-memory para cada teste."""
    config = StorageConfig(
        database_url="sqlite+aiosqlite:///:memory:"
    )
    setup_database(config)
    await init_db()
    yield
    await close_db()


@pytest.fixture
def logo_path(tmp_path: Path) -> Path:
    """Retorna diretório temporário para logos."""
    return tmp_path / "logos"


@pytest.fixture
def registry(logo_path: Path) -> BrandRegistry:
    """Cria instância do BrandRegistry com path temporário."""
    return BrandRegistry(logo_storage_path=logo_path)


class TestRegisterLogo:
    """Testes para register_logo()."""

    async def test_registra_png_com_sucesso(
        self, registry: BrandRegistry, logo_path: Path
    ):
        """Deve registrar logo PNG e retornar BrandAsset."""
        asset = await registry.register_logo(
            VALID_PNG, "logo.png"
        )

        assert asset.id is not None
        assert asset.asset_type == "logo"
        assert asset.file_path is not None
        assert asset.content_hash is not None
        assert asset.original_filename == "logo.png"
        assert asset.file_size_bytes == len(VALID_PNG)
        assert asset.created_at is not None

    async def test_registra_jpg_com_sucesso(
        self, registry: BrandRegistry
    ):
        """Deve registrar logo JPG e retornar BrandAsset."""
        asset = await registry.register_logo(
            VALID_JPG, "logo.jpg"
        )

        assert asset.asset_type == "logo"
        assert asset.original_filename == "logo.jpg"

    async def test_registra_svg_com_sucesso(
        self, registry: BrandRegistry
    ):
        """Deve registrar logo SVG e retornar BrandAsset."""
        asset = await registry.register_logo(
            VALID_SVG, "logo.svg"
        )

        assert asset.asset_type == "logo"
        assert asset.original_filename == "logo.svg"

    async def test_salva_arquivo_no_filesystem(
        self, registry: BrandRegistry, logo_path: Path
    ):
        """Deve salvar o arquivo de logo no diretório configurado."""
        await registry.register_logo(VALID_PNG, "test.png")

        # Verifica que o diretório foi criado e contém arquivo
        assert logo_path.exists()
        files = list(logo_path.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == VALID_PNG

    async def test_rejeita_formato_invalido(
        self, registry: BrandRegistry
    ):
        """Deve rejeitar imagem com formato não suportado."""
        invalid_data = b"not an image format"

        with pytest.raises(ValueError, match="Formato de imagem"):
            await registry.register_logo(
                invalid_data, "file.bmp"
            )

    async def test_rejeita_imagem_acima_5mb(
        self, registry: BrandRegistry
    ):
        """Deve rejeitar imagem que excede 5 MB."""
        # PNG header + dados grandes
        large_data = VALID_PNG[:8] + b"\x00" * (6 * 1024 * 1024)

        with pytest.raises(ValueError, match="excede tamanho"):
            await registry.register_logo(large_data, "big.png")

    async def test_rejeita_logo_duplicado(
        self, registry: BrandRegistry
    ):
        """Deve rejeitar logo com content hash já existente."""
        await registry.register_logo(VALID_PNG, "logo1.png")

        with pytest.raises(ValueError, match="já existe"):
            await registry.register_logo(
                VALID_PNG, "logo2.png"
            )

    async def test_cria_diretorio_se_nao_existe(
        self, registry: BrandRegistry, logo_path: Path
    ):
        """Deve criar o diretório de logos se não existir."""
        assert not logo_path.exists()

        await registry.register_logo(VALID_PNG, "logo.png")

        assert logo_path.exists()


class TestRegisterText:
    """Testes para register_text()."""

    async def test_registra_texto_com_sucesso(
        self, registry: BrandRegistry
    ):
        """Deve registrar texto de marca e retornar BrandAsset."""
        asset = await registry.register_text("MinhaMarca")

        assert asset.id is not None
        assert asset.asset_type == "text"
        assert asset.text_value == "MinhaMarca"
        assert asset.content_hash is not None
        assert asset.file_path is None
        assert asset.original_filename is None
        assert asset.file_size_bytes is None
        assert asset.created_at is not None

    async def test_rejeita_texto_curto_demais(
        self, registry: BrandRegistry
    ):
        """Deve rejeitar texto com menos de 2 caracteres."""
        with pytest.raises(ValueError, match="minimo"):
            await registry.register_text("a")

    async def test_rejeita_texto_longo_demais(
        self, registry: BrandRegistry
    ):
        """Deve rejeitar texto com mais de 256 caracteres."""
        long_text = "x" * 257

        with pytest.raises(ValueError, match="maximo"):
            await registry.register_text(long_text)

    async def test_rejeita_texto_apenas_espacos(
        self, registry: BrandRegistry
    ):
        """Deve rejeitar texto sem caracteres visíveis."""
        with pytest.raises(ValueError, match="visiveis"):
            await registry.register_text("   ")

    async def test_rejeita_texto_duplicado(
        self, registry: BrandRegistry
    ):
        """Deve rejeitar texto com content hash já existente."""
        await registry.register_text("BrandName")

        with pytest.raises(ValueError, match="já existe"):
            await registry.register_text("BrandName")


class TestGetAllAssets:
    """Testes para get_all_assets()."""

    async def test_retorna_lista_vazia_sem_ativos(
        self, registry: BrandRegistry
    ):
        """Deve retornar lista vazia quando não há ativos."""
        assets = await registry.get_all_assets()
        assert assets == []

    async def test_retorna_todos_ativos_registrados(
        self, registry: BrandRegistry
    ):
        """Deve retornar todos os ativos após registros."""
        await registry.register_logo(VALID_PNG, "logo.png")
        await registry.register_text("MinhaMarca")

        assets = await registry.get_all_assets()

        assert len(assets) == 2
        types = {a.asset_type for a in assets}
        assert types == {"logo", "text"}


class TestRemoveAsset:
    """Testes para remove_asset()."""

    async def test_remove_texto_com_sucesso(
        self, registry: BrandRegistry
    ):
        """Deve remover ativo de texto e retornar True."""
        asset = await registry.register_text("BrandX")

        result = await registry.remove_asset(asset.id)

        assert result is True

        # Verifica que não está mais na lista
        assets = await registry.get_all_assets()
        assert len(assets) == 0

    async def test_remove_logo_e_arquivo(
        self, registry: BrandRegistry, logo_path: Path
    ):
        """Deve remover ativo de logo e deletar arquivo físico."""
        asset = await registry.register_logo(
            VALID_PNG, "logo.png"
        )

        # Verifica que o arquivo existe
        assert asset.file_path is not None
        assert asset.file_path.exists()

        result = await registry.remove_asset(asset.id)

        assert result is True
        # Arquivo deve ter sido deletado
        assert not asset.file_path.exists()

    async def test_retorna_false_se_id_nao_existe(
        self, registry: BrandRegistry
    ):
        """Deve retornar False se o asset_id não existe."""
        result = await registry.remove_asset("id-inexistente")
        assert result is False
