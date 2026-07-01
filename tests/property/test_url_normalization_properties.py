"""Property tests para URL Normalization do Brand Watchdog.

**Validates: Requirements 1.4**

Propriedades testadas:
- Idempotência: normalize(normalize(url)) == normalize(url)
- Case insensitivity de scheme e host
- Remoção consistente de trailing slashes
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from brand_watchdog.utils.validators import URLValidator


_PBT_SETTINGS = settings(max_examples=100)

# Estratégia para gerar schemes com variações de case
_schemes = st.sampled_from([
    "http", "https", "HTTP", "HTTPS",
    "Http", "Https", "hTTp", "hTTPs",
])

# Estratégia para gerar hostnames válidos com variações de case
_hostname_labels = st.from_regex(
    r"[a-zA-Z][a-zA-Z0-9\-]{0,10}[a-zA-Z0-9]",
    fullmatch=True,
)

_hostnames = st.builds(
    lambda parts: ".".join(parts),
    st.lists(_hostname_labels, min_size=2, max_size=3),
)

# Estratégia para gerar paths opcionais
_paths = st.sampled_from([
    "", "/", "/page", "/page/", "/a/b/c",
    "/a/b/c/", "/path/to/resource",
    "/path/to/resource/",
])

# Estratégia para gerar URLs válidas com variações
_valid_urls = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}{path}",
    _schemes,
    _hostnames,
    _paths,
)


class TestURLNormalizationIdempotence:
    """Property 2: URL Normalization Idempotence.

    URLs válidas com variações de case e trailing slashes,
    normalize(normalize(url)) == normalize(url).

    **Validates: Requirements 1.4**
    """

    @_PBT_SETTINGS
    @given(url=_valid_urls)
    def test_normalize_is_idempotent(self, url: str):
        """Aplicar normalize duas vezes produz o mesmo resultado
        que aplicar uma vez."""
        validator = URLValidator()
        once = validator.normalize(url)
        twice = validator.normalize(once)
        assert twice == once, (
            f"Idempotência violada: "
            f"normalize('{url}') = '{once}', "
            f"normalize('{once}') = '{twice}'"
        )

    @_PBT_SETTINGS
    @given(url=_valid_urls)
    def test_normalize_produces_lowercase_scheme(self, url: str):
        """Scheme do resultado normalizado é sempre lowercase."""
        validator = URLValidator()
        normalized = validator.normalize(url)
        scheme = normalized.split("://")[0]
        assert scheme == scheme.lower(), (
            f"Scheme não está em lowercase: '{scheme}' "
            f"na URL normalizada '{normalized}'"
        )

    @_PBT_SETTINGS
    @given(url=_valid_urls)
    def test_normalize_produces_lowercase_host(self, url: str):
        """Hostname do resultado normalizado é sempre lowercase."""
        validator = URLValidator()
        normalized = validator.normalize(url)
        # Extrai host entre :// e a próxima / ou fim da string
        after_scheme = normalized.split("://", 1)[1]
        host = after_scheme.split("/")[0]
        assert host == host.lower(), (
            f"Host não está em lowercase: '{host}' "
            f"na URL normalizada '{normalized}'"
        )

    @_PBT_SETTINGS
    @given(url=_valid_urls)
    def test_normalize_removes_trailing_slash(self, url: str):
        """URL normalizada não termina com trailing slash
        (exceto URLs que são apenas scheme://host)."""
        validator = URLValidator()
        normalized = validator.normalize(url)
        # Extrai a parte após scheme://host
        after_scheme = normalized.split("://", 1)[1]
        if "/" in after_scheme:
            path = after_scheme[after_scheme.index("/"):]
            assert not path.endswith("/"), (
                f"Trailing slash não removida: path='{path}' "
                f"na URL normalizada '{normalized}'"
            )

    @_PBT_SETTINGS
    @given(
        scheme=_schemes,
        host=_hostnames,
        path=st.sampled_from([
            "/page", "/a/b/c", "/path/to/resource",
        ]),
    )
    def test_case_variations_normalize_to_same_result(
        self, scheme: str, host: str, path: str
    ):
        """URLs que diferem apenas em case de scheme/host devem
        normalizar para o mesmo resultado."""
        validator = URLValidator()

        url_lower = f"{scheme.lower()}://{host.lower()}{path}"
        url_upper = f"{scheme.upper()}://{host.upper()}{path}"
        url_mixed = f"{scheme}://{host}{path}"

        norm_lower = validator.normalize(url_lower)
        norm_upper = validator.normalize(url_upper)
        norm_mixed = validator.normalize(url_mixed)

        assert norm_lower == norm_upper == norm_mixed, (
            f"Variações de case produzem resultados diferentes: "
            f"lower='{norm_lower}', upper='{norm_upper}', "
            f"mixed='{norm_mixed}'"
        )

    @_PBT_SETTINGS
    @given(
        scheme=_schemes,
        host=_hostnames,
        path=st.sampled_from([
            "/page", "/a/b", "/resource",
        ]),
    )
    def test_trailing_slash_variation_normalizes_consistently(
        self, scheme: str, host: str, path: str
    ):
        """URLs com e sem trailing slash devem normalizar para
        o mesmo resultado."""
        validator = URLValidator()

        url_no_slash = f"{scheme}://{host}{path}"
        url_with_slash = f"{scheme}://{host}{path}/"

        norm_no_slash = validator.normalize(url_no_slash)
        norm_with_slash = validator.normalize(url_with_slash)

        assert norm_no_slash == norm_with_slash, (
            f"Trailing slash produz resultados diferentes: "
            f"sem_slash='{norm_no_slash}', "
            f"com_slash='{norm_with_slash}'"
        )
