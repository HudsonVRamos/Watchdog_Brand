"""Testes de integração para o fluxo completo de crawl.

Valida o fluxo integrado do Crawler: navigate → scroll → wait → capture → return.
Usa mocks da API assíncrona do Playwright (Page, Browser) para simular
o comportamento do browser sem exigir instalação real.

Requirements: 3.1, 3.2, 3.3
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.config import CrawlerConfig, StorageConfig
from brand_watchdog.crawler.crawler import Crawler
from brand_watchdog.models.dataclasses import CaptureResult


# --- Helpers para criação de mocks Playwright ---


def _make_mock_response(status: int = 200) -> MagicMock:
    """Cria mock de Response Playwright com status HTTP."""
    response = MagicMock()
    response.status = status
    return response


def _make_mock_page(
    body_scroll_heights: list[int] | None = None,
    response_status: int = 200,
) -> AsyncMock:
    """Cria mock de Page Playwright com comportamento realista.

    Args:
        body_scroll_heights: Sequência de valores retornados por
            document.body.scrollHeight (simula lazy loading).
            Default: [3000, 3000] (página estática sem lazy).
        response_status: Status HTTP da navegação.

    Returns:
        Mock da Page Playwright configurado.
    """
    if body_scroll_heights is None:
        body_scroll_heights = [3000, 3000]

    page = AsyncMock()
    page.set_default_timeout = MagicMock()

    # Mock goto retorna response com status
    mock_response = _make_mock_response(response_status)
    page.goto = AsyncMock(return_value=mock_response)

    # Mock evaluate: simula scrollHeight e scroll commands
    scroll_idx = {"current": 0}

    async def mock_evaluate(expression: str):
        if "scrollHeight" in expression:
            idx = min(
                scroll_idx["current"],
                len(body_scroll_heights) - 1,
            )
            height = body_scroll_heights[idx]
            scroll_idx["current"] += 1
            return height
        if "scrollBy" in expression or "scrollTo" in expression:
            return None
        return None

    page.evaluate = AsyncMock(side_effect=mock_evaluate)

    # Mock wait_for_load_state (network idle)
    page.wait_for_load_state = AsyncMock()

    # Mock screenshot: retorna bytes PNG fake
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    page.screenshot = AsyncMock(return_value=fake_png)

    # Mock close
    page.close = AsyncMock()

    return page


def _make_mock_browser(page: AsyncMock | None = None) -> AsyncMock:
    """Cria mock de Browser Playwright."""
    browser = AsyncMock()
    browser.is_connected = MagicMock(return_value=True)

    if page is None:
        page = _make_mock_page()

    browser.new_page = AsyncMock(return_value=page)
    browser.close = AsyncMock()
    return browser


def _make_mock_playwright(browser: AsyncMock | None = None) -> AsyncMock:
    """Cria mock de Playwright context manager."""
    if browser is None:
        browser = _make_mock_browser()

    playwright = AsyncMock()
    playwright.chromium = AsyncMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    playwright.stop = AsyncMock()
    return playwright


def _make_crawler(
    tmp_path: Path,
    viewport_width: int = 1280,
    page_timeout_seconds: int = 60,
    max_screenshot_height_px: int = 20000,
) -> Crawler:
    """Cria instância do Crawler com configurações de teste."""
    config = CrawlerConfig(
        viewport_width=viewport_width,
        page_timeout_seconds=page_timeout_seconds,
        network_idle_timeout_ms=500,
        max_screenshot_height_px=max_screenshot_height_px,
        screenshot_format="png",
    )
    storage_config = StorageConfig(
        screenshot_base_path=tmp_path / "screenshots",
    )
    return Crawler(config=config, storage_config=storage_config)


# --- Testes de Integração: Fluxo Completo de Crawl ---


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_navigates_to_url(tmp_path: Path):
    """Crawler navega para a URL alvo com viewport de 1280px (Req 3.1)."""
    page = _make_mock_page(body_scroll_heights=[2000, 2000])
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)

    with patch(
        "brand_watchdog.crawler.crawler.async_playwright"
    ) as mock_async_pw:
        mock_async_pw.return_value.start = AsyncMock(
            return_value=mock_pw
        )
        # Injeta o browser mockado diretamente
        crawler._playwright = mock_pw
        crawler._browser = browser

        result = await crawler.capture("https://example.com/page")

    # Verifica navegação para a URL correta
    page.goto.assert_called_once_with(
        "https://example.com/page",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    # Verifica que o viewport foi configurado
    browser.new_page.assert_called_once_with(
        viewport={"width": 1280, "height": 720}
    )
    assert result.success is True
    assert result.target_url == "https://example.com/page"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_scrolls_for_lazy_content(tmp_path: Path):
    """Crawler faz scroll incremental para carregar lazy content (Req 3.2)."""
    # Simula página com lazy loading: altura cresce a cada scroll
    # 3000 → 6000 → 9000 → 9000 (estabiliza)
    scroll_heights = [3000, 6000, 9000, 9000]
    page = _make_mock_page(body_scroll_heights=scroll_heights)
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://lazysite.com")

    assert result.success is True
    # Verifica que houve múltiplas chamadas evaluate (scroll + height checks)
    # Pelo menos 4 chamadas de scrollHeight + scrollBy + scrollTo(0,0)
    assert page.evaluate.call_count >= 4
    # Verifica que network idle foi aguardado durante scroll
    assert page.wait_for_load_state.call_count >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_waits_for_network_idle(tmp_path: Path):
    """Crawler aguarda network idle antes da captura (Req 3.3)."""
    page = _make_mock_page(body_scroll_heights=[5000, 5000])
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://dynamicsite.com")

    assert result.success is True
    # Verifica que wait_for_load_state("networkidle") foi chamado
    # ao menos 1 vez (durante scroll) + 1 final antes do screenshot
    calls = page.wait_for_load_state.call_args_list
    network_idle_calls = [
        c for c in calls if c.args == ("networkidle",)
    ]
    assert len(network_idle_calls) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_captures_screenshot(tmp_path: Path):
    """Crawler captura screenshot full-page após scroll e network idle."""
    page = _make_mock_page(body_scroll_heights=[4000, 4000])
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://screenshot-test.com")

    assert result.success is True
    # Verifica que screenshot foi chamado com full_page=True e type="png"
    page.screenshot.assert_called_once()
    call_kwargs = page.screenshot.call_args.kwargs
    assert call_kwargs["full_page"] is True
    assert call_kwargs["type"] == "png"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_full_flow_end_to_end(tmp_path: Path):
    """Teste end-to-end: navigate → scroll → wait → capture → CaptureResult.

    Valida o fluxo completo integrado do Crawler retornando um
    CaptureResult correto com todos os campos preenchidos.
    """
    # Simula página com lazy loading moderado
    scroll_heights = [2000, 4000, 4000]
    page = _make_mock_page(body_scroll_heights=scroll_heights)
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://fulltest.com/path")

    # Verifica resultado completo
    assert isinstance(result, CaptureResult)
    assert result.success is True
    assert result.target_url == "https://fulltest.com/path"
    assert result.page_height_px > 0
    assert result.was_truncated is False
    assert result.error_message is None
    assert result.screenshot_ref_id  # UUID não vazio
    assert result.captured_at is not None

    # Verifica que o arquivo de screenshot foi salvo
    assert result.screenshot_path.exists()
    assert result.screenshot_path.suffix == ".png"

    # Verifica sequência de operações:
    # 1. Navegação
    page.goto.assert_called_once()
    # 2. Scroll (evaluate chamado múltiplas vezes)
    assert page.evaluate.call_count >= 3
    # 3. Wait network idle
    assert page.wait_for_load_state.call_count >= 1
    # 4. Screenshot
    page.screenshot.assert_called_once()
    # 5. Page closed
    page.close.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_truncates_tall_pages(tmp_path: Path):
    """Crawler trunca páginas que excedem 20,000px (Req 3.6)."""
    # Página que cresce muito: 10000 → 25000 (excede limite)
    scroll_heights = [10000, 25000]
    page = _make_mock_page(body_scroll_heights=scroll_heights)
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path, max_screenshot_height_px=20000)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://tallpage.com")

    assert result.success is True
    assert result.was_truncated is True
    assert result.page_height_px <= 20000


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_handles_http_error(tmp_path: Path):
    """Crawler retorna falha para páginas com HTTP 4xx/5xx (Req 3.5)."""
    page = _make_mock_page(response_status=404)
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://notfound.com")

    assert result.success is False
    assert "404" in result.error_message
    # Não deve ter capturado screenshot
    page.screenshot.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_handles_timeout(tmp_path: Path):
    """Crawler retorna falha quando a página não carrega em 60s (Req 3.4)."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    page = _make_mock_page()
    # goto levanta timeout
    page.goto = AsyncMock(
        side_effect=PlaywrightTimeoutError("Timeout 60000ms exceeded")
    )
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://slow-site.com")

    assert result.success is False
    assert "Timeout" in result.error_message
    # Não deve ter capturado screenshot
    page.screenshot.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_page_closed_on_success(tmp_path: Path):
    """Page é fechada após captura bem-sucedida (cleanup de recursos)."""
    page = _make_mock_page(body_scroll_heights=[1500, 1500])
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://cleanup-test.com")

    assert result.success is True
    page.close.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_page_closed_on_error(tmp_path: Path):
    """Page é fechada mesmo quando ocorre erro (cleanup de recursos)."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    page = _make_mock_page()
    page.goto = AsyncMock(
        side_effect=PlaywrightTimeoutError("Timeout")
    )
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://error-cleanup.com")

    assert result.success is False
    page.close.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_crawl_flow_scrolls_back_to_top(tmp_path: Path):
    """Crawler rola de volta ao topo antes de capturar screenshot."""
    scroll_heights = [2000, 4000, 4000]
    page = _make_mock_page(body_scroll_heights=scroll_heights)
    browser = _make_mock_browser(page=page)
    mock_pw = _make_mock_playwright(browser=browser)

    crawler = _make_crawler(tmp_path)
    crawler._playwright = mock_pw
    crawler._browser = browser

    result = await crawler.capture("https://scroll-top.com")

    assert result.success is True
    # Verifica que window.scrollTo(0, 0) foi chamado
    scroll_to_calls = [
        c
        for c in page.evaluate.call_args_list
        if c.args and "scrollTo(0, 0)" in str(c.args[0])
    ]
    assert len(scroll_to_calls) >= 1
