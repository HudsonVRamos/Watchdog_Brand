"""Testes unitários para o TargetSiteManager.

Valida registro, remoção, listagem, validação de URL,
controle de duplicatas e limite máximo de sites.
"""

import pytest

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    init_db,
    setup_database,
)
from brand_watchdog.models.dataclasses import TargetSite
from brand_watchdog.registry.target_site_manager import TargetSiteManager


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
def manager() -> TargetSiteManager:
    """Instância do TargetSiteManager com limite padrão."""
    return TargetSiteManager()


@pytest.fixture
def manager_limit_3() -> TargetSiteManager:
    """Instância do TargetSiteManager com limite de 3 sites."""
    return TargetSiteManager(max_target_sites=3)


class TestValidateUrl:
    """Testes para validate_url()."""

    def test_url_valida_https(self, manager: TargetSiteManager):
        """Deve aceitar URL com https e hostname válido."""
        result = manager.validate_url("https://example.com")
        assert result.valid is True
        assert result.error is None

    def test_url_valida_http(self, manager: TargetSiteManager):
        """Deve aceitar URL com http e hostname válido."""
        result = manager.validate_url("http://example.com/path")
        assert result.valid is True

    def test_url_invalida_sem_scheme(self, manager: TargetSiteManager):
        """Deve rejeitar URL sem scheme http/https."""
        result = manager.validate_url("ftp://example.com")
        assert result.valid is False
        assert result.error is not None

    def test_url_vazia(self, manager: TargetSiteManager):
        """Deve rejeitar URL vazia."""
        result = manager.validate_url("")
        assert result.valid is False


class TestNormalizeUrl:
    """Testes para normalize_url()."""

    def test_lowercase_scheme_e_host(self, manager: TargetSiteManager):
        """Deve converter scheme e host para lowercase."""
        result = manager.normalize_url("HTTPS://EXAMPLE.COM/Path")
        assert result == "https://example.com/Path"

    def test_remove_trailing_slash(self, manager: TargetSiteManager):
        """Deve remover trailing slash do path."""
        result = manager.normalize_url("https://example.com/path/")
        assert result == "https://example.com/path"

    def test_idempotencia(self, manager: TargetSiteManager):
        """Deve ser idempotente: normalize(normalize(x)) == normalize(x)."""
        url = "HTTPS://Example.COM/path/"
        first = manager.normalize_url(url)
        second = manager.normalize_url(first)
        assert first == second


class TestRegister:
    """Testes para register()."""

    async def test_registro_sucesso(self, manager: TargetSiteManager):
        """Deve registrar site e retornar TargetSite com todos os campos."""
        site = await manager.register("https://example.com")
        assert isinstance(site, TargetSite)
        assert site.url == "https://example.com"
        assert site.normalized_url == "https://example.com"
        assert site.active is True
        assert site.id is not None
        assert site.created_at is not None

    async def test_registro_url_invalida(self, manager: TargetSiteManager):
        """Deve levantar ValueError para URL inválida."""
        with pytest.raises(ValueError, match="URL inválida"):
            await manager.register("ftp://invalid.com")

    async def test_registro_url_hostname_invalido(
        self, manager: TargetSiteManager
    ):
        """Deve levantar ValueError para URL com hostname inválido."""
        with pytest.raises(ValueError, match="URL inválida"):
            await manager.register("https://")

    async def test_registro_url_sem_scheme(
        self, manager: TargetSiteManager
    ):
        """Deve levantar ValueError para URL sem scheme http/https."""
        with pytest.raises(ValueError, match="URL inválida"):
            await manager.register("example.com/path")

    async def test_registro_url_duplicada(self, manager: TargetSiteManager):
        """Deve rejeitar URL duplicada (após normalização)."""
        await manager.register("https://example.com")
        with pytest.raises(ValueError, match="já existe"):
            await manager.register("https://example.com")

    async def test_registro_url_duplicada_normalizada(
        self, manager: TargetSiteManager
    ):
        """Deve rejeitar URL duplicada mesmo com case diferente."""
        await manager.register("https://Example.COM/path/")
        with pytest.raises(ValueError, match="já existe"):
            await manager.register("https://example.com/path")

    async def test_registro_limite_excedido(
        self, manager_limit_3: TargetSiteManager
    ):
        """Deve rejeitar registro quando limite máximo é atingido."""
        await manager_limit_3.register("https://site1.com")
        await manager_limit_3.register("https://site2.com")
        await manager_limit_3.register("https://site3.com")

        with pytest.raises(ValueError, match="Limite máximo"):
            await manager_limit_3.register("https://site4.com")

    async def test_registro_limite_padrao_200(
        self, manager: TargetSiteManager
    ):
        """Deve usar limite padrão de 200 sites."""
        assert manager._max_target_sites == 200


class TestRemove:
    """Testes para remove()."""

    async def test_remove_existente(self, manager: TargetSiteManager):
        """Deve remover site existente e retornar True."""
        site = await manager.register("https://example.com")
        result = await manager.remove(site.id)
        assert result is True

    async def test_remove_inexistente(self, manager: TargetSiteManager):
        """Deve retornar False para site inexistente."""
        result = await manager.remove("id-inexistente")
        assert result is False

    async def test_remove_permite_reregistro(
        self, manager: TargetSiteManager
    ):
        """Após remoção, a mesma URL pode ser registrada novamente."""
        site = await manager.register("https://example.com")
        await manager.remove(site.id)
        new_site = await manager.register("https://example.com")
        assert new_site.id != site.id


class TestListAll:
    """Testes para list_all()."""

    async def test_lista_vazia(self, manager: TargetSiteManager):
        """Deve retornar lista vazia quando não há sites."""
        sites = await manager.list_all()
        assert sites == []

    async def test_lista_sites_registrados(
        self, manager: TargetSiteManager
    ):
        """Deve retornar todos os sites registrados."""
        await manager.register("https://site1.com")
        await manager.register("https://site2.com")

        sites = await manager.list_all()
        assert len(sites) == 2
        assert all(isinstance(s, TargetSite) for s in sites)

    async def test_lista_exclui_removidos(
        self, manager: TargetSiteManager
    ):
        """Deve excluir sites removidos da listagem."""
        site1 = await manager.register("https://site1.com")
        await manager.register("https://site2.com")
        await manager.remove(site1.id)

        sites = await manager.list_all()
        assert len(sites) == 1
        assert sites[0].url == "https://site2.com"


class TestBrandSupport:
    """Testes para suporte a brand por site."""

    async def test_registro_com_brand_sky_plus(
        self, manager: TargetSiteManager
    ):
        """Deve registrar site com brand sky_plus (padrão)."""
        site = await manager.register("https://example.com")
        assert site.brand == "sky_plus"

    async def test_registro_com_brand_dgo(
        self, manager: TargetSiteManager
    ):
        """Deve registrar site com brand dgo."""
        site = await manager.register(
            "https://dgo.com", brand="dgo"
        )
        assert site.brand == "dgo"

    async def test_registro_com_brand_invalido(
        self, manager: TargetSiteManager
    ):
        """Deve rejeitar brand inválido."""
        with pytest.raises(ValueError, match="Brand inválido"):
            await manager.register(
                "https://example.com", brand="invalid"
            )

    async def test_list_all_inclui_brand(
        self, manager: TargetSiteManager
    ):
        """list_all deve incluir brand no TargetSite retornado."""
        await manager.register(
            "https://sky.com", brand="sky_plus"
        )
        await manager.register(
            "https://dgo.com", brand="dgo"
        )

        sites = await manager.list_all()
        assert len(sites) == 2
        brands = {s.url: s.brand for s in sites}
        assert brands["https://sky.com"] == "sky_plus"
        assert brands["https://dgo.com"] == "dgo"

    async def test_registro_default_brand_backward_compat(
        self, manager: TargetSiteManager
    ):
        """Sites registrados sem brand devem ter sky_plus."""
        site = await manager.register("https://old-site.com")
        assert site.brand == "sky_plus"
