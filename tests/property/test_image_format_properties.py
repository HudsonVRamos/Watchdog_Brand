"""Property tests para validação de formato de imagem.

**Validates: Requirements 2.1, 2.4**

Property 6: Image Format Validation — file headers + tamanhos variados,
aceita apenas PNG/JPG/SVG ≤ 5 MB.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from brand_watchdog.utils.validators import BrandAssetValidator
from brand_watchdog.models.dataclasses import ValidationResult


_PBT_SETTINGS = settings(max_examples=30)

# Constantes de referência
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff"


# ─── Strategies ──────────────────────────────────────────────────────


def _png_data(max_size: int = MAX_IMAGE_SIZE):
    """Gera dados PNG válidos: magic bytes + padding aleatório até max_size."""
    min_padding = 0
    max_padding = max_size - len(PNG_MAGIC)
    return st.binary(
        min_size=min_padding, max_size=max_padding
    ).map(lambda padding: PNG_MAGIC + padding)


def _jpg_data(max_size: int = MAX_IMAGE_SIZE):
    """Gera dados JPG válidos: magic bytes + padding aleatório até max_size."""
    min_padding = 0
    max_padding = max_size - len(JPG_MAGIC)
    return st.binary(
        min_size=min_padding, max_size=max_padding
    ).map(lambda padding: JPG_MAGIC + padding)


def _svg_data(max_size: int = MAX_IMAGE_SIZE):
    """Gera dados SVG válidos: header XML ou SVG + padding até max_size."""
    svg_headers = [b"<?xml", b"<svg"]
    return st.sampled_from(svg_headers).flatmap(
        lambda header: st.binary(
            min_size=0, max_size=max_size - len(header)
        ).map(lambda padding: header + padding)
    )


def _oversized_png():
    """Gera dados PNG que excedem 5 MB."""
    # Gera entre 1 e 1024 bytes extras além do limite
    extra = st.integers(min_value=1, max_value=1024)
    return extra.map(
        lambda e: PNG_MAGIC + b"\x00" * (MAX_IMAGE_SIZE - len(PNG_MAGIC) + e)
    )


def _oversized_jpg():
    """Gera dados JPG que excedem 5 MB."""
    extra = st.integers(min_value=1, max_value=1024)
    return extra.map(
        lambda e: JPG_MAGIC + b"\x00" * (MAX_IMAGE_SIZE - len(JPG_MAGIC) + e)
    )


def _oversized_svg():
    """Gera dados SVG que excedem 5 MB."""
    extra = st.integers(min_value=1, max_value=1024)
    return extra.map(
        lambda e: b"<svg" + b"x" * (MAX_IMAGE_SIZE - len(b"<svg") + e)
    )


def _invalid_magic_data():
    """Gera dados que NÃO começam com magic bytes válidos de PNG/JPG/SVG."""
    return st.binary(min_size=1, max_size=MAX_IMAGE_SIZE).filter(
        lambda data: (
            not data.startswith(PNG_MAGIC)
            and not data.startswith(JPG_MAGIC)
            and b"<?xml" not in data[:1024].lower()
            and b"<svg" not in data[:1024].lower()
        )
    )


# ─── Tests ───────────────────────────────────────────────────────────


class TestImageFormatValidation:
    """Property 6: Image Format Validation.

    Aceita apenas PNG/JPG/SVG com tamanho ≤ 5 MB.

    **Validates: Requirements 2.1, 2.4**
    """

    def setup_method(self):
        """Instancia o validador para cada teste."""
        self.validator = BrandAssetValidator()

    @_PBT_SETTINGS
    @given(data=_png_data())
    def test_valid_png_accepted(self, data: bytes):
        """PNG válido (magic correto + tamanho ≤ 5 MB) deve ser aceito."""
        result = self.validator.validate_image(data, "logo.png")
        assert result.valid is True
        assert result.error is None

    @_PBT_SETTINGS
    @given(data=_jpg_data())
    def test_valid_jpg_accepted(self, data: bytes):
        """JPG válido (magic correto + tamanho ≤ 5 MB) deve ser aceito."""
        result = self.validator.validate_image(data, "logo.jpg")
        assert result.valid is True
        assert result.error is None

    @_PBT_SETTINGS
    @given(data=_svg_data())
    def test_valid_svg_accepted(self, data: bytes):
        """SVG válido (header '<?xml' ou '<svg' + tamanho ≤ 5 MB) deve ser aceito."""
        result = self.validator.validate_image(data, "logo.svg")
        assert result.valid is True
        assert result.error is None

    @_PBT_SETTINGS
    @given(data=st.one_of(_oversized_png(), _oversized_jpg(), _oversized_svg()))
    def test_oversized_image_rejected(self, data: bytes):
        """Imagem com tamanho > 5 MB deve ser rejeitada independente do formato."""
        result = self.validator.validate_image(data, "big_image.png")
        assert result.valid is False
        assert result.error is not None
        assert "5 MB" in result.error

    @_PBT_SETTINGS
    @given(data=_invalid_magic_data())
    def test_invalid_magic_rejected(self, data: bytes):
        """Dados sem magic bytes válidos de PNG/JPG/SVG devem ser rejeitados."""
        result = self.validator.validate_image(data, "unknown.bin")
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(
        data=st.one_of(_png_data(), _jpg_data(), _svg_data(), _invalid_magic_data())
    )
    def test_valid_implies_correct_format_and_size(self, data: bytes):
        """Se validate_image retorna valid=True, então os dados iniciam com
        magic PNG/JPG ou contêm marcador SVG, E len(data) ≤ 5 MB."""
        result = self.validator.validate_image(data, "test_file.img")

        if result.valid:
            has_png_magic = data[:8] == PNG_MAGIC
            has_jpg_magic = data[:3] == JPG_MAGIC
            header_lower = data[:1024].decode("utf-8", errors="ignore").lower()
            has_svg_marker = "<?xml" in header_lower or "<svg" in header_lower

            assert has_png_magic or has_jpg_magic or has_svg_marker
            assert len(data) <= MAX_IMAGE_SIZE
