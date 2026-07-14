"""Calculador de versão do conjunto de regras de compliance.

Aplica SHA-256 sobre a concatenação ordenada (alfabeticamente pelo
caminho relativo) dos conteúdos de todos os arquivos no diretório
de regras (incluindo subdiretórios).
O formato de versão é: "v{timestamp_unix}_{hash_8_chars}".

Requisitos validados: 7.1, 7.4, 7.5
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class RuleSetDirectoryError(Exception):
    """Exceção para diretório de regras vazio ou inacessível.

    Quando levantada, o ciclo de monitoramento deve ser abortado
    com status "error".
    """


class RuleSetVersionCalculator:
    """Calcula e compara versões do conjunto de regras de compliance.

    A versão é determinada pelo hash SHA-256 dos conteúdos dos arquivos
    do diretório de regras, ordenados alfabeticamente pelo caminho relativo.
    O formato final é "v{timestamp_unix}_{hash_8_chars}" (max 30 caracteres).

    Args:
        rules_dir: Caminho para o diretório de regras (ex: watchdog_rules/).
    """

    def __init__(self, rules_dir: Path) -> None:
        self._rules_dir = rules_dir

    def calculate(self) -> str:
        """Calcula versão no formato 'v{timestamp_unix}_{hash_8_chars}'.

        Aplica SHA-256 sobre conteúdos concatenados (ordenados alfabeticamente
        pelo caminho relativo ao diretório de regras, incluindo subdiretórios).

        Returns:
            String de versão no formato "v{timestamp_unix}_{hash_8_chars}".

        Raises:
            RuleSetDirectoryError: Se o diretório não existir, estiver
                inacessível ou não contiver nenhum arquivo.
        """
        content_hash = self._compute_hash()
        timestamp = int(time.time())
        version = f"v{timestamp}_{content_hash[:8]}"
        return version

    def has_changed(self, previous_version: str | None) -> bool:
        """Compara hash atual com versão anterior.

        Extrai a porção de hash (últimos 8 caracteres após o
        último '_') da versão anterior e compara com o hash atual
        do diretório. Registra em log quando há mudança de versão.

        Args:
            previous_version: Versão anterior no formato "v{ts}_{hash_8}"
                ou None se não houver versão anterior.

        Returns:
            True se o hash mudou (ou se não há versão anterior),
            False caso contrário.
        """
        current_hash = self._compute_hash()[:8]

        if previous_version is None:
            logger.info(
                "Primeira execução de versionamento de regras. "
                "Hash atual: %s",
                current_hash,
            )
            return True

        previous_hash = self._extract_hash(previous_version)

        if current_hash != previous_hash:
            current_version = f"v{int(time.time())}_{current_hash}"
            logger.info(
                "Versão de regras alterada: %s -> %s",
                previous_version,
                current_version,
            )
            return True

        return False

    def _compute_hash(self) -> str:
        """Calcula o SHA-256 sobre os conteúdos concatenados.

        Os arquivos são ordenados alfabeticamente pelo caminho
        relativo ao diretório de regras. Inclui todos os arquivos
        em subdiretórios.

        Returns:
            Hash SHA-256 hexadecimal completo (64 caracteres).

        Raises:
            RuleSetDirectoryError: Se o diretório não existir, estiver
                inacessível ou vazio.
        """
        self._validate_directory()

        # Coleta todos os arquivos recursivamente
        files = sorted(
            (f for f in self._rules_dir.rglob("*") if f.is_file()),
            key=lambda f: str(f.relative_to(self._rules_dir)),
        )

        if not files:
            raise RuleSetDirectoryError(
                f"Diretório de regras está vazio: {self._rules_dir}"
            )

        # Calcula SHA-256 sobre a concatenação ordenada dos conteúdos
        sha256 = hashlib.sha256()
        for file_path in files:
            try:
                sha256.update(file_path.read_bytes())
            except (OSError, PermissionError) as e:
                raise RuleSetDirectoryError(
                    "Não foi possível ler arquivo de regras "
                    f"'{file_path}': {e}"
                ) from e

        return sha256.hexdigest()

    def _validate_directory(self) -> None:
        """Valida que o diretório de regras existe e é acessível.

        Raises:
            RuleSetDirectoryError: Se o diretório não existir ou
                não for um diretório.
        """
        if not self._rules_dir.exists():
            raise RuleSetDirectoryError(
                f"Diretório de regras não encontrado: "
                f"{self._rules_dir}"
            )

        if not self._rules_dir.is_dir():
            raise RuleSetDirectoryError(
                f"Caminho não é um diretório: "
                f"{self._rules_dir}"
            )

        # Tenta listar para verificar permissão de acesso
        try:
            next(self._rules_dir.iterdir(), None)
        except PermissionError as e:
            raise RuleSetDirectoryError(
                "Sem permissão para acessar diretório de regras: "
                f"{self._rules_dir}"
            ) from e

    @staticmethod
    def _extract_hash(version: str) -> str:
        """Extrai a porção de hash de uma string de versão.

        Args:
            version: String no formato "v{timestamp}_{hash_8_chars}".

        Returns:
            Os 8 caracteres de hash da versão.
        """
        # O hash são os últimos 8 caracteres após o último '_'
        parts = version.rsplit("_", maxsplit=1)
        if len(parts) == 2:
            return parts[1]
        return ""
