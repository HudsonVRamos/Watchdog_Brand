"""Property tests para URL Validation do Brand Watchdog.

**Validates: Requirements 1.1, 1.3, 1.5**

Property 1: URL Validation Correctness — strings aleatórias com schemes,
hostnames e paths, aceita apenas URLs válidas.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from brand_watchdog.utils.validators import URLValidator


_PBT_SETTINGS = settings(max_examples=100)


# --- Strategies ---

def _valid_hostname_label():
    """Gera um label válido de hostname (RFC 1123).

    Regras:
    - Começa e termina com alfanumérico
    - Meio pode ter hífens
    - Max 63 chars por label
    """
    # Labels de 1 char: apenas alfanumérico
    single_char = st.from_regex(r"[a-zA-Z0-9]", fullmatch=True)
    # Labels de 2+ chars: começa/termina alnum, meio alnum/hifens
    multi_char = st.builds(
        lambda first, middle, last: first + middle + last,
        first=st.from_regex(r"[a-zA-Z0-9]", fullmatch=True),
        middle=st.from_regex(r"[a-zA-Z0-9\-]{0,10}", fullmatch=True),
        last=st.from_regex(r"[a-zA-Z0-9]", fullmatch=True),
    )
    return st.one_of(single_char, multi_char)


def _valid_hostname():
    """Gera hostname válido com 1-4 labels separados por pontos."""
    return st.lists(
        _valid_hostname_label(),
        min_size=1,
        max_size=4,
    ).map(lambda labels: ".".join(labels))


def _valid_path():
    """Gera path opcional para URL."""
    return st.one_of(
        st.just(""),
        st.from_regex(r"/[a-zA-Z0-9/_\-]{0,50}", fullmatch=True),
    )


@st.composite
def valid_urls(draw):
    """Strategy que gera URLs válidas (http/https + hostname RFC 1123 + path)."""
    scheme = draw(st.sampled_from(["http", "https"]))
    hostname = draw(_valid_hostname())
    path = draw(_valid_path())
    url = f"{scheme}://{hostname}{path}"
    # Garante que não excede o limite de 2048 chars
    return url[:2048]


@st.composite
def urls_with_invalid_scheme(draw):
    """Strategy que gera URLs com schemes inválidos."""
    scheme = draw(st.sampled_from(["ftp", "ssh", "ws", "file", "mailto", "tcp"]))
    hostname = draw(_valid_hostname())
    path = draw(_valid_path())
    return f"{scheme}://{hostname}{path}"


@st.composite
def urls_too_long(draw):
    """Strategy que gera URLs que excedem 2048 chars."""
    scheme = draw(st.sampled_from(["http", "https"]))
    # Usa hostname fixo curto para manter a strategy simples
    hostname = "example.com"
    base = f"{scheme}://{hostname}/"
    # Gera extra_length entre 1 e 500 chars além do limite
    extra = draw(st.integers(min_value=1, max_value=500))
    padding_needed = 2049 - len(base) + extra
    padding = "a" * padding_needed
    url = base + padding
    assert len(url) > 2048
    return url


@st.composite
def urls_with_invalid_hostname(draw):
    """Strategy que gera URLs com hostnames inválidos (chars não RFC 1123).

    Usamos apenas hostnames que o urlparse() interpreta corretamente como
    hostname (não como userinfo, fragment, etc.) mas que violam RFC 1123.
    """
    scheme = draw(st.sampled_from(["http", "https"]))
    # Hostnames inválidos que urlparse extrai corretamente:
    # - Começa com hífem
    # - Termina com hífem
    # - Contém underscore
    # - Contém caracteres especiais que urlparse mantém no hostname
    invalid_hostname = draw(st.sampled_from([
        "-invalid.com",
        "invalid-.com",
        "host_name.com",
        "-a.com",
        "a-.b.com",
        "--double.com",
    ]))
    path = draw(_valid_path())
    return f"{scheme}://{invalid_hostname}{path}"


@st.composite
def urls_without_hostname(draw):
    """Strategy que gera URLs sem hostname."""
    scheme = draw(st.sampled_from(["http", "https"]))
    # Variações que resultam em hostname vazio
    return draw(st.sampled_from([
        f"{scheme}://",
        f"{scheme}:///path",
        f"{scheme}://:8080/path",
    ]))


# --- Testes ---

class TestURLValidationCorrectness:
    """Property 1: URL Validation Correctness.

    Strings aleatórias com schemes, hostnames e paths, aceita apenas URLs
    válidas.

    **Validates: Requirements 1.1, 1.3, 1.5**
    """

    @_PBT_SETTINGS
    @given(url=valid_urls())
    def test_valid_urls_always_accepted(self, url):
        """URLs com scheme http/https + hostname válido + path opcional
        e comprimento <= 2048 devem sempre ser aceitas."""
        validator = URLValidator()
        result = validator.validate(url)
        assert result.valid is True, (
            f"URL válida rejeitada: {url!r}, erro: {result.error}"
        )

    @_PBT_SETTINGS
    @given(url=urls_with_invalid_scheme())
    def test_invalid_scheme_always_rejected(self, url):
        """URLs com scheme diferente de http/https devem sempre ser
        rejeitadas."""
        validator = URLValidator()
        result = validator.validate(url)
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(url=urls_too_long())
    def test_urls_exceeding_max_length_always_rejected(self, url):
        """URLs com mais de 2048 caracteres devem sempre ser rejeitadas."""
        validator = URLValidator()
        result = validator.validate(url)
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(url=urls_with_invalid_hostname())
    def test_invalid_hostname_always_rejected(self, url):
        """URLs com hostname inválido (não RFC 1123) devem sempre ser
        rejeitadas."""
        validator = URLValidator()
        result = validator.validate(url)
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(url=urls_without_hostname())
    def test_urls_without_hostname_always_rejected(self, url):
        """URLs sem hostname devem sempre ser rejeitadas."""
        validator = URLValidator()
        result = validator.validate(url)
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(url=valid_urls())
    def test_valid_url_has_correct_properties(self, url):
        """Propriedade: qualquer URL que passa na validação possui scheme
        em {http, https}, hostname não vazio e comprimento <= 2048."""
        validator = URLValidator()
        result = validator.validate(url)

        if result.valid:
            # Verifica propriedades invariantes de URLs válidas
            assert len(url) <= 2048
            assert url.startswith("http://") or url.startswith("https://")
            # Extrai hostname da URL validada
            from urllib.parse import urlparse
            parsed = urlparse(url)
            assert parsed.hostname is not None
            assert len(parsed.hostname) > 0

    @_PBT_SETTINGS
    @given(text=st.text(min_size=0, max_size=100))
    def test_arbitrary_strings_never_crash(self, text):
        """O validador nunca lança exceção, independente da entrada."""
        validator = URLValidator()
        result = validator.validate(text)
        # Sempre retorna um ValidationResult
        assert isinstance(result.valid, bool)
        if not result.valid:
            assert result.error is not None
