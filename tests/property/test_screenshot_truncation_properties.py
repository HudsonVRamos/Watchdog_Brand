"""Property tests para truncamento de altura de screenshot.

Validates: Requirements 3.6

Property 7: Screenshot Height Truncation — páginas com alturas variadas
(1-50000px), trunca em 20000px.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from brand_watchdog.crawler import compute_screenshot_truncation


_PBT_SETTINGS = settings(max_examples=30)

# Constante padrão do sistema conforme design
_MAX_HEIGHT = 20000


class TestScreenshotHeightTruncation:
    """Property 7: Screenshot Height Truncation.

    Para qualquer altura de página entre 1 e 50000px, o resultado
    deve ser truncado em 20000px (max_screenshot_height_px default).

    **Validates: Requirements 3.6**
    """

    @_PBT_SETTINGS
    @given(page_height=st.integers(min_value=1, max_value=50000))
    def test_effective_height_never_exceeds_max(self, page_height: int):
        """A altura efetiva do screenshot nunca excede o limite
        máximo configurado."""
        effective_height, _ = compute_screenshot_truncation(
            page_height, _MAX_HEIGHT
        )
        assert effective_height <= _MAX_HEIGHT

    @_PBT_SETTINGS
    @given(page_height=st.integers(min_value=1, max_value=50000))
    def test_effective_height_is_min_of_page_and_max(
        self, page_height: int
    ):
        """A altura efetiva é sempre min(page_height, max_height)."""
        effective_height, _ = compute_screenshot_truncation(
            page_height, _MAX_HEIGHT
        )
        assert effective_height == min(page_height, _MAX_HEIGHT)

    @_PBT_SETTINGS
    @given(
        page_height=st.integers(min_value=_MAX_HEIGHT + 1, max_value=50000)
    )
    def test_pages_above_max_are_truncated(self, page_height: int):
        """Páginas com altura > max_screenshot_height_px devem
        ter was_truncated=True."""
        _, was_truncated = compute_screenshot_truncation(
            page_height, _MAX_HEIGHT
        )
        assert was_truncated is True

    @_PBT_SETTINGS
    @given(page_height=st.integers(min_value=1, max_value=_MAX_HEIGHT))
    def test_pages_at_or_below_max_are_not_truncated(
        self, page_height: int
    ):
        """Páginas com altura <= max_screenshot_height_px devem
        ter was_truncated=False."""
        _, was_truncated = compute_screenshot_truncation(
            page_height, _MAX_HEIGHT
        )
        assert was_truncated is False

    @_PBT_SETTINGS
    @given(
        page_height=st.integers(min_value=1, max_value=50000),
        max_height=st.integers(min_value=1, max_value=50000),
    )
    def test_truncation_consistent_with_arbitrary_max(
        self, page_height: int, max_height: int
    ):
        """Para qualquer limite máximo arbitrário, a lógica de
        truncamento permanece consistente."""
        effective_height, was_truncated = compute_screenshot_truncation(
            page_height, max_height
        )
        assert effective_height == min(page_height, max_height)
        assert was_truncated == (page_height > max_height)
