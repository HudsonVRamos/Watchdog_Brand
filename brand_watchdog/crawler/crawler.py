"""Crawler para captura de screenshots full-page usando Playwright.

Responsável por navegar nos sites-alvo e capturar screenshots completos,
incluindo conteúdo carregado via lazy-loading.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from brand_watchdog.config import CrawlerConfig, StorageConfig
from brand_watchdog.models.dataclasses import CaptureResult

logger = logging.getLogger(__name__)


def compute_screenshot_truncation(
    page_height: int,
    max_screenshot_height_px: int = 20000,
) -> tuple[int, bool]:
    """Calcula a altura efetiva do screenshot e se houve truncamento.

    Função pura que encapsula a lógica de truncamento usada pelo Crawler.
    Para qualquer página com altura superior ao limite configurado, a
    captura é truncada no valor máximo.

    Args:
        page_height: Altura real da página em pixels (≥ 1).
        max_screenshot_height_px: Altura máxima permitida para o
            screenshot (default: 20000px).

    Returns:
        Tupla (effective_height, was_truncated):
            - effective_height: min(page_height, max_screenshot_height_px)
            - was_truncated: True se page_height > max_screenshot_height_px
    """
    effective_height = min(page_height, max_screenshot_height_px)
    was_truncated = page_height > max_screenshot_height_px
    return effective_height, was_truncated


class Crawler:
    """Crawler Playwright para captura de screenshots full-page.

    Gerencia o ciclo de vida do browser e implementa scroll incremental
    para garantir carregamento de conteúdo lazy-loaded antes da captura.
    """

    def __init__(
        self,
        config: CrawlerConfig,
        storage_config: StorageConfig | None = None,
    ) -> None:
        """Inicializa o Crawler.

        Args:
            config: Configuração do crawler (viewport, timeouts, etc.).
            storage_config: Configuração de armazenamento para
                definir o diretório base dos screenshots.
                Se None, usa ./data/screenshots.
        """
        self._config = config
        self._storage_config = storage_config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def _ensure_browser(self) -> Browser:
        """Garante que uma instância do browser está disponível.

        Cria o browser lazily na primeira chamada. Utiliza Chromium
        em modo headless para performance e compatibilidade.

        Returns:
            Instância ativa do browser Playwright.
        """
        if self._browser is None or not self._browser.is_connected():
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
            )
            logger.info("Browser Chromium iniciado em modo headless")
        return self._browser

    async def capture(self, target_url: str) -> CaptureResult:
        """Captura screenshot full-page do site alvo.

        Fluxo:
        1. Cria nova página com viewport configurado
        2. Navega até a URL com timeout configurado
        3. Verifica status HTTP (4xx/5xx = skip)
        4. Faz scroll incremental para carregar lazy content
        5. Aguarda network idle
        6. Captura screenshot full-page (com truncamento se necessário)
        7. Salva screenshot e retorna resultado

        Args:
            target_url: URL do site-alvo para captura.

        Returns:
            CaptureResult com dados da captura ou indicação de falha.
        """
        screenshot_ref_id = str(uuid.uuid4())
        captured_at = datetime.now(timezone.utc)
        page: Page | None = None

        try:
            browser = await self._ensure_browser()
            page = await browser.new_page(
                viewport={"width": self._config.viewport_width, "height": 720}
            )
            page.set_default_timeout(
                self._config.page_timeout_seconds * 1000
            )

            # Navegação com tratamento de timeout
            response: Response | None = None
            try:
                response = await page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=self._config.page_timeout_seconds * 1000,
                )
            except PlaywrightTimeoutError:
                logger.error(
                    "Timeout de %ds ao carregar %s",
                    self._config.page_timeout_seconds,
                    target_url,
                )
                return CaptureResult(
                    target_url=target_url,
                    screenshot_path=Path(""),
                    screenshot_ref_id=screenshot_ref_id,
                    captured_at=captured_at,
                    page_height_px=0,
                    was_truncated=False,
                    success=False,
                    error_message=(
                        f"Timeout de {self._config.page_timeout_seconds}s "
                        f"ao carregar {target_url}"
                    ),
                )

            # Verificação de status HTTP (4xx/5xx)
            if response is not None and response.status >= 400:
                logger.error(
                    "HTTP %d ao acessar %s — site ignorado",
                    response.status,
                    target_url,
                )
                return CaptureResult(
                    target_url=target_url,
                    screenshot_path=Path(""),
                    screenshot_ref_id=screenshot_ref_id,
                    captured_at=captured_at,
                    page_height_px=0,
                    was_truncated=False,
                    success=False,
                    error_message=(
                        f"HTTP {response.status} ao acessar {target_url}"
                    ),
                )

            # Scroll para carregar conteúdo lazy-loaded
            page_height = await self._scroll_for_lazy_content(page)

            # Aguarda estabilização final da rede
            try:
                await page.wait_for_load_state("networkidle")
            except PlaywrightTimeoutError:
                logger.warning(
                    "Timeout aguardando network idle em %s, "
                    "prosseguindo com captura",
                    target_url,
                )

            # Determina se a página será truncada
            was_truncated = (
                page_height >= self._config.max_screenshot_height_px
            )

            # Captura screenshot
            screenshot_bytes = await self._take_screenshot(
                page, page_height
            )

            # Salva screenshot no filesystem
            screenshot_path = self._get_screenshot_path(screenshot_ref_id)
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(screenshot_bytes)

            logger.info(
                "Screenshot capturado: %s (%dpx, truncado=%s)",
                target_url,
                page_height,
                was_truncated,
            )

            return CaptureResult(
                target_url=target_url,
                screenshot_path=screenshot_path,
                screenshot_ref_id=screenshot_ref_id,
                captured_at=captured_at,
                page_height_px=page_height,
                was_truncated=was_truncated,
                success=True,
            )

        except PlaywrightTimeoutError:
            logger.error(
                "Timeout inesperado durante captura de %s", target_url
            )
            return CaptureResult(
                target_url=target_url,
                screenshot_path=Path(""),
                screenshot_ref_id=screenshot_ref_id,
                captured_at=captured_at,
                page_height_px=0,
                was_truncated=False,
                success=False,
                error_message=f"Timeout durante captura de {target_url}",
            )
        except Exception as exc:
            logger.error(
                "Erro inesperado ao capturar %s: %s",
                target_url,
                str(exc),
            )
            return CaptureResult(
                target_url=target_url,
                screenshot_path=Path(""),
                screenshot_ref_id=screenshot_ref_id,
                captured_at=captured_at,
                page_height_px=0,
                was_truncated=False,
                success=False,
                error_message=f"Erro ao capturar {target_url}: {str(exc)}",
            )
        finally:
            if page is not None:
                await page.close()

    async def _scroll_for_lazy_content(self, page: Page) -> int:
        """Rola a página para forçar carregamento de conteúdo lazy-loaded.

        Algoritmo:
        1. Obtém altura total inicial do body
        2. Rola incrementalmente (viewport height por vez)
        3. Após cada scroll, aguarda estabilização da rede
        4. Repete até atingir o final ou limite de 20,000px
        5. Rola de volta ao topo para screenshot consistente

        Args:
            page: Página Playwright ativa.

        Returns:
            Altura total da página em pixels (limitada ao máximo configurado).
        """
        viewport_height = self._config.viewport_width  # 1280 — proporção 1:1
        max_height = self._config.max_screenshot_height_px
        previous_height = 0

        while True:
            current_height = await page.evaluate(
                "document.body.scrollHeight"
            )

            if current_height == previous_height:
                break  # Sem novo conteúdo carregado

            if current_height >= max_height:
                logger.warning(
                    "Página excede limite de %d px, será truncada",
                    max_height,
                )
                break

            # Rola para a próxima seção
            await page.evaluate(f"window.scrollBy(0, {viewport_height})")

            # Aguarda carregamento de novos elementos
            try:
                await page.wait_for_load_state("networkidle")
            except PlaywrightTimeoutError:
                logger.debug(
                    "Timeout no network idle durante scroll, continuando"
                )

            await asyncio.sleep(0.3)  # Buffer para rendering
            previous_height = current_height

        # Volta ao topo para screenshot completo
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.2)

        final_height = await page.evaluate("document.body.scrollHeight")
        return min(final_height, max_height)

    async def _take_screenshot(
        self, page: Page, page_height: int
    ) -> bytes:
        """Captura screenshot da página com limite de altura.

        Se a altura da página excede max_screenshot_height_px, utiliza
        o parâmetro clip para truncar a captura.

        Args:
            page: Página Playwright ativa.
            page_height: Altura real da página (já limitada pelo scroll).

        Returns:
            Bytes do screenshot PNG.
        """
        max_height = self._config.max_screenshot_height_px

        if page_height >= max_height:
            # Captura com clip para truncar na altura máxima
            screenshot_bytes = await page.screenshot(
                full_page=True,
                type="png",
                clip={
                    "x": 0,
                    "y": 0,
                    "width": self._config.viewport_width,
                    "height": max_height,
                },
            )
        else:
            # Captura full-page normal
            screenshot_bytes = await page.screenshot(
                full_page=True,
                type="png",
            )

        return screenshot_bytes

    def _get_screenshot_path(self, screenshot_ref_id: str) -> Path:
        """Gera o caminho do arquivo de screenshot.

        Args:
            screenshot_ref_id: UUID de referência do screenshot.

        Returns:
            Path completo para o arquivo PNG.
        """
        if self._storage_config is not None:
            base_path = self._storage_config.screenshot_base_path
        else:
            base_path = Path("./data/screenshots")

        return base_path / f"{screenshot_ref_id}.png"

    async def close(self) -> None:
        """Encerra o browser e libera recursos.

        Deve ser chamado ao finalizar o uso do crawler para
        garantir que não haja processos órfãos do browser.
        """
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
            logger.info("Browser encerrado")

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
