"""Testes unitários para o utilitário de hashing."""

import hashlib

import pytest

from brand_watchdog.utils.hashing import hash_content, hash_text


class TestHashContent:
    """Testa hash_content para dados binários."""

    def test_retorna_hex_digest_64_caracteres(self):
        data = b"conteudo qualquer"
        resultado = hash_content(data)
        assert len(resultado) == 64

    def test_retorna_string_lowercase(self):
        data = b"\x00\x01\x02\x03"
        resultado = hash_content(data)
        assert resultado == resultado.lower()

    def test_hash_correto_para_bytes_conhecidos(self):
        data = b"hello"
        esperado = hashlib.sha256(b"hello").hexdigest()
        assert hash_content(data) == esperado

    def test_bytes_vazios(self):
        esperado = hashlib.sha256(b"").hexdigest()
        assert hash_content(b"") == esperado

    def test_deterministico_mesma_entrada(self):
        data = b"dados identicos"
        assert hash_content(data) == hash_content(data)

    def test_entradas_diferentes_produzem_hashes_diferentes(self):
        assert hash_content(b"abc") != hash_content(b"abd")

    def test_dados_binarios_grandes(self):
        data = bytes(range(256)) * 1000
        resultado = hash_content(data)
        assert len(resultado) == 64
        assert resultado == hashlib.sha256(data).hexdigest()


class TestHashText:
    """Testa hash_text para strings de texto."""

    def test_retorna_hex_digest_64_caracteres(self):
        resultado = hash_text("marca exemplo")
        assert len(resultado) == 64

    def test_retorna_string_lowercase(self):
        resultado = hash_text("TEXTO")
        assert resultado == resultado.lower()

    def test_hash_correto_para_texto_conhecido(self):
        texto = "Brand Name"
        esperado = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        assert hash_text(texto) == esperado

    def test_texto_vazio(self):
        esperado = hashlib.sha256(b"").hexdigest()
        assert hash_text("") == esperado

    def test_deterministico_mesma_entrada(self):
        texto = "mesmo texto"
        assert hash_text(texto) == hash_text(texto)

    def test_textos_diferentes_produzem_hashes_diferentes(self):
        assert hash_text("abc") != hash_text("abd")

    def test_caracteres_unicode(self):
        texto = "café ☕ résumé"
        esperado = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        assert hash_text(texto) == esperado

    def test_consistencia_com_hash_content(self):
        """hash_text deve ser equivalente a hash_content com UTF-8."""
        texto = "test string"
        assert hash_text(texto) == hash_content(texto.encode("utf-8"))
