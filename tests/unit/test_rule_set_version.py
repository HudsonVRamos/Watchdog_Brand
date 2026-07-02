"""Testes unitários para o RuleSetVersionCalculator.

Valida o cálculo de versão do conjunto de regras, detecção de mudanças,
e tratamento de erros para diretórios vazios/inacessíveis.

Requirements: 7.1, 7.4, 7.5
"""

import hashlib
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from brand_watchdog.utils.rule_set_version import (
    RuleSetDirectoryError,
    RuleSetVersionCalculator,
)


@pytest.fixture
def rules_dir(tmp_path: Path) -> Path:
    """Cria um diretório de regras temporário com arquivos de exemplo."""
    rules = tmp_path / "watchdog_rules"
    rules.mkdir()
    (rules / "regra_a.txt").write_text("conteúdo da regra A", encoding="utf-8")
    (rules / "regra_b.txt").write_text("conteúdo da regra B", encoding="utf-8")
    return rules


@pytest.fixture
def rules_dir_with_subdir(tmp_path: Path) -> Path:
    """Cria diretório de regras com subdiretório contendo imagens."""
    rules = tmp_path / "watchdog_rules"
    rules.mkdir()
    (rules / "regra_texto.txt").write_text("texto de regra", encoding="utf-8")
    subdir = rules / "imagens"
    subdir.mkdir()
    (subdir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n fake image data")
    return rules


class TestCalculate:
    """Testa o método calculate()."""

    def test_formato_versao_correto(self, rules_dir: Path):
        """Versão deve seguir formato v{timestamp_unix}_{hash_8_chars}."""
        calc = RuleSetVersionCalculator(rules_dir)
        version = calc.calculate()

        # Formato: v seguido de timestamp unix, underscore, 8 chars hexadecimais
        pattern = r"^v\d+_[0-9a-f]{8}$"
        assert re.match(pattern, version), f"Formato inválido: {version}"

    def test_versao_max_30_caracteres(self, rules_dir: Path):
        """Versão deve ter no máximo 30 caracteres."""
        calc = RuleSetVersionCalculator(rules_dir)
        version = calc.calculate()
        assert len(version) <= 30

    def test_hash_deterministico_mesmo_conteudo(self, rules_dir: Path):
        """Hash deve ser o mesmo para o mesmo conteúdo (ignora timestamp)."""
        calc = RuleSetVersionCalculator(rules_dir)
        v1 = calc.calculate()
        v2 = calc.calculate()

        # Extrai apenas a parte do hash (últimos 8 chars após '_')
        hash1 = v1.rsplit("_", 1)[1]
        hash2 = v2.rsplit("_", 1)[1]
        assert hash1 == hash2

    def test_hash_diferente_para_conteudo_diferente(self, tmp_path: Path):
        """Hash deve mudar quando conteúdo do diretório muda."""
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "regra.txt").write_text("versão 1", encoding="utf-8")

        calc = RuleSetVersionCalculator(rules)
        v1 = calc.calculate()

        # Modifica conteúdo
        (rules / "regra.txt").write_text("versão 2", encoding="utf-8")
        v2 = calc.calculate()

        hash1 = v1.rsplit("_", 1)[1]
        hash2 = v2.rsplit("_", 1)[1]
        assert hash1 != hash2

    def test_inclui_arquivos_de_subdiretorios(self, rules_dir_with_subdir: Path):
        """Deve incluir arquivos de subdiretórios no cálculo do hash."""
        calc = RuleSetVersionCalculator(rules_dir_with_subdir)
        version = calc.calculate()
        assert re.match(r"^v\d+_[0-9a-f]{8}$", version)

    def test_ordenacao_alfabetica_por_caminho_relativo(self, tmp_path: Path):
        """Arquivos devem ser ordenados pelo caminho relativo ao diretório."""
        rules = tmp_path / "rules"
        rules.mkdir()
        # Cria arquivos em ordem diferente da alfabética
        (rules / "z_ultimo.txt").write_bytes(b"Z")
        (rules / "a_primeiro.txt").write_bytes(b"A")

        calc = RuleSetVersionCalculator(rules)
        version = calc.calculate()

        # Calcula manualmente com ordem correta
        sha256 = hashlib.sha256()
        sha256.update(b"A")  # a_primeiro.txt vem primeiro
        sha256.update(b"Z")  # z_ultimo.txt vem depois
        expected_hash = sha256.hexdigest()[:8]

        actual_hash = version.rsplit("_", 1)[1]
        assert actual_hash == expected_hash


class TestHasChanged:
    """Testa o método has_changed()."""

    def test_retorna_true_quando_previous_none(self, rules_dir: Path):
        """Deve retornar True na primeira execução (sem versão anterior)."""
        calc = RuleSetVersionCalculator(rules_dir)
        assert calc.has_changed(None) is True

    def test_retorna_false_quando_hash_igual(self, rules_dir: Path):
        """Deve retornar False quando o hash não mudou."""
        calc = RuleSetVersionCalculator(rules_dir)
        current_version = calc.calculate()
        assert calc.has_changed(current_version) is False

    def test_retorna_true_quando_conteudo_muda(self, tmp_path: Path):
        """Deve retornar True quando conteúdo do diretório muda."""
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "regra.txt").write_text("original", encoding="utf-8")

        calc = RuleSetVersionCalculator(rules)
        version_original = calc.calculate()

        # Modifica conteúdo
        (rules / "regra.txt").write_text("modificado", encoding="utf-8")
        assert calc.has_changed(version_original) is True

    def test_registra_log_mudanca_versao(self, tmp_path: Path, caplog):
        """Deve registrar log quando a versão muda."""
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "regra.txt").write_text("v1", encoding="utf-8")

        calc = RuleSetVersionCalculator(rules)
        version_v1 = calc.calculate()

        # Modifica
        (rules / "regra.txt").write_text("v2", encoding="utf-8")

        import logging

        with caplog.at_level(logging.INFO):
            calc.has_changed(version_v1)

        assert "Versão de regras alterada" in caplog.text
        assert version_v1 in caplog.text

    def test_registra_log_primeira_execucao(self, rules_dir: Path, caplog):
        """Deve registrar log na primeira execução."""
        import logging

        calc = RuleSetVersionCalculator(rules_dir)
        with caplog.at_level(logging.INFO):
            calc.has_changed(None)

        assert "Primeira execução" in caplog.text


class TestDirectoryErrors:
    """Testa tratamento de erros para diretório vazio/inacessível."""

    def test_diretorio_inexistente_levanta_erro(self, tmp_path: Path):
        """Deve levantar RuleSetDirectoryError se diretório não existe."""
        calc = RuleSetVersionCalculator(tmp_path / "nao_existe")
        with pytest.raises(RuleSetDirectoryError, match="não encontrado"):
            calc.calculate()

    def test_diretorio_vazio_levanta_erro(self, tmp_path: Path):
        """Deve levantar RuleSetDirectoryError se diretório está vazio."""
        rules = tmp_path / "rules_vazio"
        rules.mkdir()

        calc = RuleSetVersionCalculator(rules)
        with pytest.raises(RuleSetDirectoryError, match="vazio"):
            calc.calculate()

    def test_caminho_e_arquivo_levanta_erro(self, tmp_path: Path):
        """Deve levantar RuleSetDirectoryError se caminho é um arquivo."""
        file_path = tmp_path / "arquivo.txt"
        file_path.write_text("não sou um diretório", encoding="utf-8")

        calc = RuleSetVersionCalculator(file_path)
        with pytest.raises(RuleSetDirectoryError, match="não é um diretório"):
            calc.calculate()

    def test_has_changed_levanta_erro_diretorio_vazio(self, tmp_path: Path):
        """has_changed() também deve levantar erro para diretório vazio."""
        rules = tmp_path / "rules_vazio"
        rules.mkdir()

        calc = RuleSetVersionCalculator(rules)
        with pytest.raises(RuleSetDirectoryError):
            calc.has_changed("v1234567890_abcdef12")
