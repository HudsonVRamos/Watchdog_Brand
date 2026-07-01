"""Testes unitarios para BrandAssetValidator."""

import pytest

from brand_watchdog.utils.validators import BrandAssetValidator


@pytest.fixture
def validator() -> BrandAssetValidator:
    """Instancia do validador para uso nos testes."""
    return BrandAssetValidator()


class TestValidateImage:
    """Testes para validacao de imagens."""

    def test_png_valido(self, validator: BrandAssetValidator):
        """PNG valido com magic bytes corretos deve ser aceito."""
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = validator.validate_image(data, "logo.png")
        assert result.valid is True
        assert result.error is None

    def test_jpg_valido(self, validator: BrandAssetValidator):
        """JPG valido com magic bytes corretos deve ser aceito."""
        data = b"\xff\xd8\xff" + b"\x00" * 100
        result = validator.validate_image(data, "foto.jpg")
        assert result.valid is True
        assert result.error is None

    def test_svg_com_tag_xml(self, validator: BrandAssetValidator):
        """SVG com declaracao <?xml deve ser aceito."""
        data = b'<?xml version="1.0"?><svg></svg>'
        result = validator.validate_image(data, "icone.svg")
        assert result.valid is True
        assert result.error is None

    def test_svg_com_tag_svg(self, validator: BrandAssetValidator):
        """SVG com tag <svg direta deve ser aceito."""
        svg = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        data = svg.encode("utf-8")
        result = validator.validate_image(data, "grafico.svg")
        assert result.valid is True
        assert result.error is None

    def test_formato_invalido(self, validator: BrandAssetValidator):
        """Formato nao suportado deve ser rejeitado."""
        data = b"GIF89a" + b"\x00" * 100
        result = validator.validate_image(data, "animacao.gif")
        assert result.valid is False
        assert "nao suportado" in result.error

    def test_imagem_excede_5mb(
        self, validator: BrandAssetValidator
    ):
        """Imagem maior que 5 MB deve ser rejeitada."""
        # 5 MB + 1 byte
        data = (
            b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)
        )
        result = validator.validate_image(data, "enorme.png")
        assert result.valid is False
        assert "5 MB" in result.error

    def test_imagem_exatamente_5mb(
        self, validator: BrandAssetValidator
    ):
        """Imagem com exatamente 5 MB deve ser aceita."""
        png_magic = b"\x89PNG\r\n\x1a\n"
        padding = b"\x00" * (5 * 1024 * 1024 - len(png_magic))
        data = png_magic + padding
        result = validator.validate_image(data, "limite.png")
        assert result.valid is True

    def test_dados_vazios(self, validator: BrandAssetValidator):
        """Dados vazios devem ser rejeitados (formato invalido)."""
        result = validator.validate_image(b"", "vazio.png")
        assert result.valid is False

    def test_svg_case_insensitive(
        self, validator: BrandAssetValidator
    ):
        """Deteccao de SVG deve ser case-insensitive."""
        svg = "<SVG xmlns='http://www.w3.org/2000/svg'></SVG>"
        data = svg.encode("utf-8")
        result = validator.validate_image(data, "upper.svg")
        assert result.valid is True


class TestValidateText:
    """Testes para validacao de texto de marca."""

    def test_texto_valido(self, validator: BrandAssetValidator):
        """Texto com comprimento e chars visiveis validos."""
        result = validator.validate_text("MinhaMarca")
        assert result.valid is True
        assert result.error is None

    def test_texto_minimo_valido(
        self, validator: BrandAssetValidator
    ):
        """Texto com exatamente 2 caracteres visiveis."""
        result = validator.validate_text("AB")
        assert result.valid is True

    def test_texto_maximo_valido(
        self, validator: BrandAssetValidator
    ):
        """Texto com exatamente 256 caracteres."""
        text = "A" * 256
        result = validator.validate_text(text)
        assert result.valid is True

    def test_texto_muito_curto(
        self, validator: BrandAssetValidator
    ):
        """Texto com menos de 2 caracteres deve ser rejeitado."""
        result = validator.validate_text("A")
        assert result.valid is False
        assert "minimo" in result.error

    def test_texto_vazio(self, validator: BrandAssetValidator):
        """Texto vazio deve ser rejeitado."""
        result = validator.validate_text("")
        assert result.valid is False

    def test_texto_muito_longo(
        self, validator: BrandAssetValidator
    ):
        """Texto com mais de 256 caracteres deve ser rejeitado."""
        text = "B" * 257
        result = validator.validate_text(text)
        assert result.valid is False
        assert "maximo" in result.error

    def test_texto_apenas_whitespace(
        self, validator: BrandAssetValidator
    ):
        """Texto com apenas espacos deve ser rejeitado."""
        result = validator.validate_text("     ")
        assert result.valid is False
        assert "visiveis" in result.error

    def test_texto_um_char_visivel(
        self, validator: BrandAssetValidator
    ):
        """Texto com apenas 1 char visivel deve ser rejeitado."""
        result = validator.validate_text("  A  ")
        assert result.valid is False
        assert "visiveis" in result.error

    def test_texto_dois_chars_visiveis_com_espacos(
        self, validator: BrandAssetValidator
    ):
        """Texto com 2 chars visiveis entre espacos."""
        result = validator.validate_text(" A B ")
        assert result.valid is True
