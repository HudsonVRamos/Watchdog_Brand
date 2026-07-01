"""Validadores de entrada (URLs, formatos, limites)."""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from brand_watchdog.models.dataclasses import ValidationResult


class URLValidator:
    """Valida e normaliza URLs de Target Sites.

    Validacao:
        - Scheme deve ser http ou https
        - Hostname deve ser sintaticamente valido (RFC 1123)
        - Comprimento maximo de 2048 caracteres

    Normalizacao:
        - Scheme em lowercase
        - Hostname em lowercase
        - Remove trailing slash do path
        - Idempotente: normalize(normalize(url)) == normalize(url)
    """

    MAX_URL_LENGTH = 2048
    VALID_SCHEMES = {"http", "https"}

    # Regex para hostname valido (RFC 1123)
    # Cada label: comeca/termina com alfanumerico,
    # meio pode ter hifens, max 63 chars por label
    HOSTNAME_PATTERN = re.compile(
        r"^[a-zA-Z0-9]"
        r"([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
        r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
    )

    def validate(self, url: str) -> ValidationResult:
        """Valida URL conforme regras de negocio.

        Regras:
            - Comprimento maximo de 2048 caracteres
            - Scheme deve ser http ou https
            - Hostname valido (RFC 1123)

        Args:
            url: URL a ser validada.

        Returns:
            ValidationResult indicando se a URL eh valida.
        """
        if not url or not url.strip():
            return ValidationResult(
                valid=False,
                error="URL nao pode ser vazia",
            )

        if len(url) > self.MAX_URL_LENGTH:
            return ValidationResult(
                valid=False,
                error=(
                    "URL excede comprimento maximo de "
                    f"{self.MAX_URL_LENGTH} caracteres"
                ),
            )

        try:
            parsed = urlparse(url)
        except Exception:
            return ValidationResult(
                valid=False,
                error="URL com formato invalido",
            )

        if parsed.scheme.lower() not in self.VALID_SCHEMES:
            return ValidationResult(
                valid=False,
                error="URL deve conter scheme http ou https",
            )

        hostname = parsed.hostname
        if not hostname:
            return ValidationResult(
                valid=False,
                error=(
                    "URL deve conter hostname "
                    "sintaticamente valido"
                ),
            )

        if not self.HOSTNAME_PATTERN.match(hostname):
            return ValidationResult(
                valid=False,
                error=(
                    "URL deve conter hostname "
                    "sintaticamente valido"
                ),
            )

        return ValidationResult(valid=True, error=None)

    def normalize(self, url: str) -> str:
        """Normaliza URL para comparacao de duplicatas.

        Transformacoes aplicadas:
            - Scheme em lowercase
            - Hostname (netloc) em lowercase
            - Remove trailing slash do path

        Idempotente: normalize(normalize(url)) == normalize(url)

        Args:
            url: URL a ser normalizada.

        Returns:
            URL normalizada como string.
        """
        parsed = urlparse(url)

        # Lowercase no scheme e netloc
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Remove trailing slash do path
        path = parsed.path.rstrip("/")

        # Reconstroi a URL normalizada
        normalized = urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        ))

        return normalized


class BrandAssetValidator:
    """Validador de ativos de marca (imagens e textos).

    Valida formato e tamanho de imagens (PNG, JPG, SVG) e regras
    de texto de marca (comprimento e caracteres visiveis).
    """

    # Tamanho maximo de imagem: 5 MB
    MAX_IMAGE_SIZE_BYTES: int = 5 * 1024 * 1024

    # Magic bytes para deteccao de formato
    PNG_MAGIC: bytes = b"\x89PNG\r\n\x1a\n"
    JPG_MAGIC: bytes = b"\xff\xd8\xff"

    # Limites de texto de marca
    MIN_TEXT_LENGTH: int = 2
    MAX_TEXT_LENGTH: int = 256
    MIN_VISIBLE_CHARS: int = 2

    def validate_image(
        self, data: bytes, filename: str
    ) -> ValidationResult:
        """Valida formato e tamanho de imagem de brand asset.

        Verifica:
        - Formato suportado (PNG, JPG ou SVG) via magic bytes
        - Tamanho maximo de 5 MB

        Args:
            data: Bytes do conteudo da imagem.
            filename: Nome original do arquivo.

        Returns:
            ValidationResult indicando se a imagem eh valida.
        """
        # Verificar tamanho
        if len(data) > self.MAX_IMAGE_SIZE_BYTES:
            size_mb = len(data) / (1024 * 1024)
            return ValidationResult(
                valid=False,
                error=(
                    "Imagem excede tamanho maximo de 5 MB "
                    f"(tamanho atual: {size_mb:.2f} MB)"
                ),
            )

        # Verificar formato via magic bytes
        if self._is_png(data):
            return ValidationResult(valid=True, error=None)

        if self._is_jpg(data):
            return ValidationResult(valid=True, error=None)

        if self._is_svg(data):
            return ValidationResult(valid=True, error=None)

        return ValidationResult(
            valid=False,
            error=(
                "Formato de imagem nao suportado. "
                "Apenas PNG, JPG e SVG sao aceitos"
            ),
        )

    def validate_text(self, text: str) -> ValidationResult:
        """Valida texto de marca conforme regras de negocio.

        Verifica:
        - Comprimento entre 2 e 256 caracteres
        - Pelo menos 2 caracteres visiveis (nao-whitespace)

        Args:
            text: Texto de marca a ser validado.

        Returns:
            ValidationResult indicando se o texto eh valido.
        """
        text_length = len(text)

        if text_length < self.MIN_TEXT_LENGTH:
            return ValidationResult(
                valid=False,
                error=(
                    "Texto de marca deve ter no minimo "
                    f"{self.MIN_TEXT_LENGTH} caracteres "
                    f"(comprimento atual: {text_length})"
                ),
            )

        if text_length > self.MAX_TEXT_LENGTH:
            return ValidationResult(
                valid=False,
                error=(
                    "Texto de marca deve ter no maximo "
                    f"{self.MAX_TEXT_LENGTH} caracteres "
                    f"(comprimento atual: {text_length})"
                ),
            )

        # Contar caracteres visiveis (nao-whitespace)
        visible_chars = sum(
            1 for ch in text if not ch.isspace()
        )

        if visible_chars < self.MIN_VISIBLE_CHARS:
            return ValidationResult(
                valid=False,
                error=(
                    "Texto de marca deve conter pelo menos "
                    f"{self.MIN_VISIBLE_CHARS} caracteres "
                    "visiveis (caracteres visiveis "
                    f"encontrados: {visible_chars})"
                ),
            )

        return ValidationResult(valid=True, error=None)

    def _is_png(self, data: bytes) -> bool:
        """Verifica se dados correspondem a PNG."""
        return data[:8] == self.PNG_MAGIC

    def _is_jpg(self, data: bytes) -> bool:
        """Verifica se dados correspondem a JPG."""
        return data[:3] == self.JPG_MAGIC

    def _is_svg(self, data: bytes) -> bool:
        """Verifica se dados correspondem a SVG.

        Decodifica os primeiros 1024 bytes como UTF-8
        e verifica presenca de '<?xml' ou '<svg'.
        """
        try:
            header = data[:1024].decode(
                "utf-8", errors="ignore"
            ).lower()
            return "<?xml" in header or "<svg" in header
        except (UnicodeDecodeError, ValueError):
            return False
