"""Property tests para Brand Registry.

**Validates: Requirements 2.3, 2.5**

Property 3: Brand Asset Registration Round-Trip — assets válidos
registrados devem aparecer em get_all_assets().

Property 4: Brand Asset Deduplication — registro duplicado (mesmo content)
deve ser rejeitado com ValueError.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    init_db,
    setup_database,
)
from brand_watchdog.registry.brand_registry import BrandRegistry


_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# -- Estratégias de geração --

# Caracteres visíveis para geração de textos de marca válidos
_VISIBLE_CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "!@#$%^&*()-_=+[]{}|;:',.<>?/~`"
)


@st.composite
def valid_brand_texts(draw: st.DrawFn) -> str:
    """Gera textos válidos para registro como brand asset.

    Requisitos:
    - Comprimento entre 2 e 256 caracteres
    - Pelo menos 2 caracteres visíveis (não-whitespace)
    """
    # Gerar entre 2 e 256 caracteres visíveis (garante ≥2 visíveis)
    num_visible = draw(st.integers(min_value=2, max_value=128))
    text = draw(
        st.text(
            alphabet=_VISIBLE_CHARS,
            min_size=num_visible,
            max_size=num_visible,
        )
    )
    return text


class TestBrandAssetRegistrationRoundTrip:
    """Property 3: Brand Asset Registration Round-Trip.

    Assets válidos registrados devem aparecer em get_all_assets().

    **Validates: Requirements 2.3**
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self, tmp_path: Path):
        """Configura banco in-memory e diretório temporário para cada teste."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:",
        )
        setup_database(config)
        await init_db()
        self._logo_path = tmp_path / "logos"
        self._logo_path.mkdir()
        self._registry = BrandRegistry(
            logo_storage_path=self._logo_path
        )
        yield
        await close_db()

    @_PBT_SETTINGS
    @given(text=valid_brand_texts())
    async def test_registered_text_appears_in_all_assets(
        self, text: str
    ):
        """Texto válido registrado deve aparecer em get_all_assets()."""
        # Registrar texto
        asset = await self._registry.register_text(text)

        # Buscar todos os assets
        all_assets = await self._registry.get_all_assets()

        # Verificar que o asset registrado está na lista
        asset_ids = [a.id for a in all_assets]
        assert asset.id in asset_ids, (
            f"Asset registrado (id={asset.id}) não encontrado "
            f"em get_all_assets()"
        )

        # Verificar que o texto está correto
        matching = [a for a in all_assets if a.id == asset.id]
        assert len(matching) == 1
        assert matching[0].text_value == text
        assert matching[0].asset_type == "text"
        assert matching[0].content_hash is not None

        # Limpar para próxima iteração do Hypothesis
        await self._registry.remove_asset(asset.id)


class TestBrandAssetDeduplication:
    """Property 4: Brand Asset Deduplication.

    Registro duplicado (mesmo content) deve ser rejeitado com ValueError.

    **Validates: Requirements 2.5**
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self, tmp_path: Path):
        """Configura banco in-memory e diretório temporário para cada teste."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:",
        )
        setup_database(config)
        await init_db()
        self._logo_path = tmp_path / "logos"
        self._logo_path.mkdir()
        self._registry = BrandRegistry(
            logo_storage_path=self._logo_path
        )
        yield
        await close_db()

    @_PBT_SETTINGS
    @given(text=valid_brand_texts())
    async def test_duplicate_text_registration_raises(
        self, text: str
    ):
        """Registrar o mesmo texto duas vezes deve levantar ValueError."""
        # Primeiro registro deve ter sucesso
        asset = await self._registry.register_text(text)

        # Segundo registro do mesmo texto deve falhar
        with pytest.raises(ValueError, match="já existe"):
            await self._registry.register_text(text)

        # Limpar para próxima iteração do Hypothesis
        await self._registry.remove_asset(asset.id)

    @_PBT_SETTINGS
    @given(data=st.binary(min_size=16, max_size=1024))
    async def test_duplicate_logo_registration_raises(
        self, data: bytes
    ):
        """Registrar o mesmo logo (mesmo conteúdo) duas vezes deve
        levantar ValueError."""
        # Criar dados PNG válidos (magic bytes + conteúdo)
        png_header = b"\x89PNG\r\n\x1a\n"
        image_data = png_header + data

        filename = "test_logo.png"

        # Primeiro registro deve ter sucesso
        asset = await self._registry.register_logo(
            image_data, filename
        )

        # Segundo registro do mesmo conteúdo deve falhar
        with pytest.raises(ValueError, match="já existe"):
            await self._registry.register_logo(
                image_data, filename
            )

        # Limpar para próxima iteração do Hypothesis
        await self._registry.remove_asset(asset.id)
