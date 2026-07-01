"""Testes unitarios para URLValidator."""

import pytest

from brand_watchdog.utils.validators import URLValidator


@pytest.fixture
def validator():
    """Instancia do URLValidator para testes."""
    return URLValidator()


class TestURLValidatorValidate:
    """Testes para o metodo validate()."""

    def test_url_http_valida(self, validator):
        """URL http valida deve passar."""
        result = validator.validate("http://example.com")
        assert result.valid is True
        assert result.error is None

    def test_url_https_valida(self, validator):
        """URL https valida deve passar."""
        result = validator.validate("https://example.com/path")
        assert result.valid is True
        assert result.error is None

    def test_url_com_path_e_query(self, validator):
        """URL com path e query string deve passar."""
        result = validator.validate(
            "https://example.com/path/to/page?q=test"
        )
        assert result.valid is True

    def test_url_vazia_rejeitada(self, validator):
        """URL vazia deve ser rejeitada."""
        result = validator.validate("")
        assert result.valid is False
        assert result.error is not None

    def test_url_apenas_espacos_rejeitada(self, validator):
        """URL com apenas espacos deve ser rejeitada."""
        result = validator.validate("   ")
        assert result.valid is False

    def test_url_sem_scheme_rejeitada(self, validator):
        """URL sem scheme http/https deve ser rejeitada."""
        result = validator.validate("ftp://example.com")
        assert result.valid is False
        assert "scheme" in result.error.lower()

    def test_url_scheme_invalido(self, validator):
        """URL com scheme invalido deve ser rejeitada."""
        result = validator.validate("file:///etc/passwd")
        assert result.valid is False

    def test_url_sem_hostname(self, validator):
        """URL sem hostname deve ser rejeitada."""
        result = validator.validate("http://")
        assert result.valid is False

    def test_url_hostname_com_underscore(self, validator):
        """Hostname com underscore nao eh valido RFC 1123."""
        result = validator.validate("http://invalid_host.com")
        assert result.valid is False

    def test_url_hostname_comeca_com_hifen(self, validator):
        """Hostname que comeca com hifen eh invalido."""
        result = validator.validate("http://-invalid.com")
        assert result.valid is False

    def test_url_excede_2048_chars(self, validator):
        """URL com mais de 2048 caracteres deve ser rejeitada."""
        long_url = "https://example.com/" + "a" * 2040
        result = validator.validate(long_url)
        assert result.valid is False
        assert "2048" in result.error

    def test_url_exatamente_2048_chars_valida(self, validator):
        """URL com exatamente 2048 caracteres deve passar."""
        # http://example.com/ = 19 chars, precisamos de 2029 no path
        base = "https://example.com/"
        path = "a" * (2048 - len(base))
        url = base + path
        assert len(url) == 2048
        result = validator.validate(url)
        assert result.valid is True

    def test_url_com_subdominios(self, validator):
        """URL com multiplos subdominios deve passar."""
        result = validator.validate(
            "https://sub.domain.example.com/page"
        )
        assert result.valid is True

    def test_url_com_porta(self, validator):
        """URL com numero de porta deve passar."""
        result = validator.validate("https://example.com:8080/api")
        assert result.valid is True

    def test_hostname_apenas_numeros(self, validator):
        """Hostname com apenas numeros eh valido (RFC 1123)."""
        result = validator.validate("http://123.456.789.0")
        assert result.valid is True


class TestURLValidatorNormalize:
    """Testes para o metodo normalize()."""

    def test_scheme_para_lowercase(self, validator):
        """Scheme deve ser convertido para lowercase."""
        result = validator.normalize("HTTP://example.com/path")
        assert result.startswith("http://")

    def test_host_para_lowercase(self, validator):
        """Hostname deve ser convertido para lowercase."""
        result = validator.normalize("https://EXAMPLE.COM/path")
        assert "example.com" in result

    def test_remove_trailing_slash(self, validator):
        """Trailing slash do path deve ser removido."""
        result = validator.normalize("https://example.com/path/")
        assert result == "https://example.com/path"

    def test_remove_trailing_slash_root(self, validator):
        """Trailing slash em URL raiz deve ser removido."""
        result = validator.normalize("https://example.com/")
        assert result == "https://example.com"

    def test_idempotente(self, validator):
        """normalize(normalize(url)) == normalize(url)."""
        url = "HTTPS://Example.COM/Path/To/Page/"
        first = validator.normalize(url)
        second = validator.normalize(first)
        assert first == second

    def test_idempotente_com_query(self, validator):
        """Idempotencia funciona com query strings."""
        url = "HTTPS://Example.COM/path/?q=Test"
        first = validator.normalize(url)
        second = validator.normalize(first)
        assert first == second

    def test_preserva_path(self, validator):
        """Path (exceto trailing slash) deve ser preservado."""
        result = validator.normalize(
            "https://example.com/Path/To/Page"
        )
        assert "/Path/To/Page" in result

    def test_preserva_query_string(self, validator):
        """Query string deve ser preservada."""
        result = validator.normalize(
            "https://example.com/path?key=value"
        )
        assert "key=value" in result

    def test_preserva_fragment(self, validator):
        """Fragment deve ser preservado."""
        result = validator.normalize(
            "https://example.com/path#section"
        )
        assert "#section" in result

    def test_url_ja_normalizada(self, validator):
        """URL ja normalizada nao deve ser alterada."""
        url = "https://example.com/path"
        result = validator.normalize(url)
        assert result == url
