"""Property tests para validação de texto de marca (Brand Text Validation).

**Validates: Requirements 2.2, 2.6**

Property 5: Brand Text Validation — strings de 0-300 chars com mix de
whitespace, aceita apenas strings com 2-256 chars e ≥2 chars visíveis.
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from brand_watchdog.utils.validators import BrandAssetValidator


_PBT_SETTINGS = settings(max_examples=100)

# Instância compartilhada do validador
_validator = BrandAssetValidator()


# -- Estratégias de geração --

# Caracteres que incluem whitespace e caracteres visíveis
_WHITESPACE_CHARS = " \t\n\r\x0b\x0c"
_VISIBLE_CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "!@#$%^&*()-_=+[]{}|;:',.<>?/~`"
    "áéíóúàèìòùãõâêîôûäëïöü"
)
_ALL_CHARS = _WHITESPACE_CHARS + _VISIBLE_CHARS


def _count_visible(text: str) -> int:
    """Conta caracteres visíveis (não-whitespace) em uma string."""
    return sum(1 for ch in text if not ch.isspace())


@st.composite
def valid_brand_texts(draw):
    """Gera textos válidos: 2-256 chars com pelo menos 2 visíveis."""
    # Gerar pelo menos 2 caracteres visíveis
    num_visible = draw(st.integers(min_value=2, max_value=256))
    visible_part = draw(
        st.text(
            alphabet=_VISIBLE_CHARS,
            min_size=num_visible,
            max_size=num_visible,
        )
    )
    # Adicionar whitespace opcional sem ultrapassar 256
    max_ws = 256 - len(visible_part)
    if max_ws > 0:
        num_ws = draw(st.integers(min_value=0, max_value=max_ws))
        ws_part = draw(
            st.text(
                alphabet=_WHITESPACE_CHARS,
                min_size=num_ws,
                max_size=num_ws,
            )
        )
    else:
        ws_part = ""

    # Misturar visíveis + whitespace via shuffle simplificado
    combined = list(visible_part + ws_part)
    # Usar permutação determinística via draw
    indices = list(range(len(combined)))
    shuffled_indices = draw(st.permutations(indices))
    result = "".join(combined[i] for i in shuffled_indices)

    return result


@st.composite
def too_short_texts(draw):
    """Gera textos com 0 ou 1 caractere (muito curtos)."""
    length = draw(st.integers(min_value=0, max_value=1))
    return draw(
        st.text(alphabet=_ALL_CHARS, min_size=length, max_size=length)
    )


@st.composite
def too_long_texts(draw):
    """Gera textos com 257-300 caracteres (muito longos)."""
    length = draw(st.integers(min_value=257, max_value=300))
    return draw(
        st.text(alphabet=_ALL_CHARS, min_size=length, max_size=length)
    )


@st.composite
def insufficient_visible_texts(draw):
    """Gera textos com 2-256 chars mas com menos de 2 chars visíveis.

    Inclui: strings só de whitespace ou com exatamente 1 char visível.
    """
    length = draw(st.integers(min_value=2, max_value=256))
    num_visible = draw(st.integers(min_value=0, max_value=1))

    if num_visible == 0:
        # Apenas whitespace
        text = draw(
            st.text(
                alphabet=_WHITESPACE_CHARS,
                min_size=length,
                max_size=length,
            )
        )
    else:
        # Exatamente 1 char visível + rest whitespace
        visible_char = draw(
            st.text(
                alphabet=_VISIBLE_CHARS,
                min_size=1,
                max_size=1,
            )
        )
        ws_count = length - 1
        ws_part = draw(
            st.text(
                alphabet=_WHITESPACE_CHARS,
                min_size=ws_count,
                max_size=ws_count,
            )
        )
        combined = list(visible_char + ws_part)
        indices = list(range(len(combined)))
        shuffled_indices = draw(st.permutations(indices))
        text = "".join(combined[i] for i in shuffled_indices)

    return text


class TestBrandTextValidation:
    """Property 5: Brand Text Validation.

    Strings de 0-300 chars com mix de whitespace, aceita apenas strings
    com 2-256 chars e ≥2 chars visíveis.

    **Validates: Requirements 2.2, 2.6**
    """

    @_PBT_SETTINGS
    @given(text=valid_brand_texts())
    def test_valid_texts_accepted(self, text: str):
        """Textos com 2-256 chars e ≥2 visíveis devem ser aceitos."""
        result = _validator.validate_text(text)
        assert result.valid is True
        assert result.error is None

    @_PBT_SETTINGS
    @given(text=too_short_texts())
    def test_too_short_texts_rejected(self, text: str):
        """Textos com 0-1 caracteres devem ser rejeitados."""
        result = _validator.validate_text(text)
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(text=too_long_texts())
    def test_too_long_texts_rejected(self, text: str):
        """Textos com 257-300 caracteres devem ser rejeitados."""
        result = _validator.validate_text(text)
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(text=insufficient_visible_texts())
    def test_insufficient_visible_chars_rejected(self, text: str):
        """Textos com <2 chars visíveis (whitespace-only ou 1 visível)
        devem ser rejeitados."""
        result = _validator.validate_text(text)
        assert result.valid is False
        assert result.error is not None

    @_PBT_SETTINGS
    @given(
        text=st.text(
            alphabet=st.characters(), min_size=0, max_size=300
        )
    )
    def test_valid_implies_correct_invariants(self, text: str):
        """Se validate_text retorna valid=True, então:
        - len(text) está em [2, 256]
        - text contém pelo menos 2 chars não-whitespace
        """
        result = _validator.validate_text(text)
        if result.valid:
            assert 2 <= len(text) <= 256, (
                f"Texto válido com comprimento {len(text)} fora de [2, 256]"
            )
            visible = _count_visible(text)
            assert visible >= 2, (
                f"Texto válido com apenas {visible} chars visíveis"
            )
