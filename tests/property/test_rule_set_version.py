"""Property test para determinismo do versionamento de regras.

# Feature: architecture-evolution, Property 10: Determinismo do Versionamento de Regras

**Validates: Requirements 7.1**

Para qualquer diretório `watchdog_rules/` com conteúdo fixo, o
`RuleSetVersionCalculator.calculate()` SHALL produzir a mesma porção
de hash em execuções consecutivas. Para conteúdos diferentes, a porção
de hash SHALL ser diferente. O formato SHALL ser
"v{timestamp_unix}_{hash_8_chars}" com exatamente 8 caracteres hexadecimais.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.utils.rule_set_version import RuleSetVersionCalculator


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)

# -- Formato esperado da versão --
_VERSION_PATTERN = re.compile(r"^v(\d+)_([0-9a-f]{8})$")


# -- Estratégias de geração de dados (otimizadas para velocidade) --

# Nomes de arquivo simples e rápidos de gerar
_filename_strategy = st.builds(
    lambda prefix, ext: f"{prefix}.{ext}",
    prefix=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
        min_size=1,
        max_size=8,
    ),
    ext=st.sampled_from(["txt", "xlsx", "json", "png", "csv"]),
)

# Conteúdo de arquivo binário não-vazio
_file_content_strategy = st.binary(min_size=1, max_size=512)


@st.composite
def rule_directory_contents(
    draw: st.DrawFn,
    *,
    min_files: int = 1,
    max_files: int = 4,
) -> dict[str, bytes]:
    """Gera um mapeamento de nome_arquivo -> conteúdo para um diretório de regras.

    Garante que há pelo menos `min_files` arquivos com nomes únicos.
    """
    num_files = draw(st.integers(min_value=min_files, max_value=max_files))
    # Gera lista de nomes únicos
    names = draw(
        st.lists(
            _filename_strategy,
            min_size=num_files,
            max_size=num_files,
            unique=True,
        )
    )
    files: dict[str, bytes] = {}
    for name in names:
        content = draw(_file_content_strategy)
        files[name] = content
    return files


@st.composite
def two_different_directory_contents(
    draw: st.DrawFn,
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Gera dois mapeamentos de diretório com conteúdos garantidamente diferentes.

    Pelo menos um arquivo deve ter conteúdo diferente entre os dois diretórios,
    ou os diretórios devem ter conjuntos de arquivos diferentes.
    """
    contents_a = draw(rule_directory_contents())

    # Gera segundo diretório baseado no primeiro, com modificação garantida
    # Estratégia: copia o primeiro e modifica um byte do primeiro arquivo
    contents_b = dict(contents_a)
    first_key = next(iter(contents_b))
    original = contents_b[first_key]
    # Modifica pelo menos um byte (inversão simples)
    modified = bytes([(b + 1) % 256 for b in original])
    contents_b[first_key] = modified

    return contents_a, contents_b


def _create_temp_dir_with_files(files: dict[str, bytes]) -> Path:
    """Cria um diretório temporário com os arquivos especificados."""
    tmp_dir = Path(tempfile.mkdtemp())
    for name, content in files.items():
        file_path = tmp_dir / name
        file_path.write_bytes(content)
    return tmp_dir


class TestRuleSetVersionDeterminism:
    """Property 10: Determinismo do Versionamento de Regras.

    **Validates: Requirements 7.1**
    """

    @_PBT_SETTINGS
    @given(files=rule_directory_contents())
    def test_same_content_produces_same_hash(
        self, files: dict[str, bytes]
    ) -> None:
        """Para conteúdo fixo, execuções consecutivas SHALL produzir o mesmo hash."""
        tmp_dir = _create_temp_dir_with_files(files)
        try:
            calculator = RuleSetVersionCalculator(tmp_dir)

            version_1 = calculator.calculate()
            version_2 = calculator.calculate()

            # Extrai a porção de hash (últimos 8 chars após último '_')
            hash_1 = version_1.rsplit("_", maxsplit=1)[1]
            hash_2 = version_2.rsplit("_", maxsplit=1)[1]

            assert hash_1 == hash_2, (
                f"Hash divergiu para mesmo conteúdo: "
                f"'{hash_1}' != '{hash_2}'. "
                f"Versões: '{version_1}' vs '{version_2}'"
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @_PBT_SETTINGS
    @given(data=two_different_directory_contents())
    def test_different_content_produces_different_hash(
        self,
        data: tuple[dict[str, bytes], dict[str, bytes]],
    ) -> None:
        """Para conteúdos diferentes, os hashes SHALL ser diferentes."""
        files_a, files_b = data

        tmp_dir_a = _create_temp_dir_with_files(files_a)
        tmp_dir_b = _create_temp_dir_with_files(files_b)
        try:
            calc_a = RuleSetVersionCalculator(tmp_dir_a)
            calc_b = RuleSetVersionCalculator(tmp_dir_b)

            version_a = calc_a.calculate()
            version_b = calc_b.calculate()

            hash_a = version_a.rsplit("_", maxsplit=1)[1]
            hash_b = version_b.rsplit("_", maxsplit=1)[1]

            assert hash_a != hash_b, (
                f"Conteúdos diferentes produziram mesmo hash: '{hash_a}'. "
                f"Dir A: {sorted(files_a.keys())}, Dir B: {sorted(files_b.keys())}"
            )
        finally:
            shutil.rmtree(tmp_dir_a, ignore_errors=True)
            shutil.rmtree(tmp_dir_b, ignore_errors=True)

    @_PBT_SETTINGS
    @given(files=rule_directory_contents())
    def test_version_format_is_correct(
        self, files: dict[str, bytes]
    ) -> None:
        """O formato SHALL ser 'v{timestamp_unix}_{hash_8_chars}' com 8 hex chars."""
        tmp_dir = _create_temp_dir_with_files(files)
        try:
            calculator = RuleSetVersionCalculator(tmp_dir)
            version = calculator.calculate()

            match = _VERSION_PATTERN.match(version)
            assert match is not None, (
                f"Versão '{version}' não corresponde ao formato esperado "
                f"'v{{timestamp_unix}}_{{hash_8_chars}}'. "
                f"Pattern: {_VERSION_PATTERN.pattern}"
            )

            timestamp_str = match.group(1)
            hash_str = match.group(2)

            # Timestamp deve ser um inteiro positivo razoável (Unix epoch)
            timestamp = int(timestamp_str)
            assert timestamp > 0, (
                f"Timestamp deve ser positivo, obteve: {timestamp}"
            )

            # Hash deve ter exatamente 8 caracteres hexadecimais
            assert len(hash_str) == 8, (
                f"Hash deve ter exatamente 8 chars, obteve {len(hash_str)}: '{hash_str}'"
            )
            assert all(c in "0123456789abcdef" for c in hash_str), (
                f"Hash contém caracteres não-hexadecimais: '{hash_str}'"
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
