"""Utilitários de hashing para deduplicação de assets.

Usa SHA-256 para gerar hashes determinísticos de conteúdo
binário (imagens) e texto (nomes de marca), garantindo
deduplicação no Brand Registry.
"""

import hashlib


def hash_content(data: bytes) -> str:
    """Retorna o SHA-256 hex digest de dados binários.

    Args:
        data: Conteúdo em bytes (ex: imagem PNG/JPG/SVG).

    Returns:
        String hexadecimal lowercase de 64 caracteres.
    """
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    """Retorna o SHA-256 hex digest de uma string de texto.

    Codifica o texto em UTF-8 antes de calcular o hash.

    Args:
        text: Texto a ser hashado (ex: nome de marca).

    Returns:
        String hexadecimal lowercase de 64 caracteres.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
