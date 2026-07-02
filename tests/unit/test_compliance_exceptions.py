"""Testes para a hierarquia de exceções de compliance.

Valida que a hierarquia de herança está correta e que
as exceções podem ser instanciadas e capturadas adequadamente.

Requisitos cobertos: 7.6, 9.4
"""

import pytest

from brand_watchdog.analyzer.compliance_exceptions import (
    AnalysisIncompleteError,
    ComplianceError,
    ComplianceParseError,
    CompliancePersistenceError,
)


class TestComplianceErrorHierarchy:
    """Testes para a hierarquia de herança das exceções."""

    def test_compliance_error_herda_de_exception(self):
        """ComplianceError deve herdar diretamente de Exception."""
        assert issubclass(ComplianceError, Exception)

    def test_analysis_incomplete_herda_de_compliance_error(self):
        """AnalysisIncompleteError deve herdar de ComplianceError."""
        assert issubclass(AnalysisIncompleteError, ComplianceError)

    def test_compliance_parse_herda_de_compliance_error(self):
        """ComplianceParseError deve herdar de ComplianceError."""
        assert issubclass(ComplianceParseError, ComplianceError)

    def test_compliance_persistence_herda_de_compliance_error(self):
        """CompliancePersistenceError deve herdar de ComplianceError."""
        assert issubclass(CompliancePersistenceError, ComplianceError)

    def test_todas_excepcoes_sao_capturadas_por_compliance_error(self):
        """Todas as exceções filhas devem ser capturáveis via ComplianceError."""
        with pytest.raises(ComplianceError):
            raise AnalysisIncompleteError("bedrock falhou")

        with pytest.raises(ComplianceError):
            raise ComplianceParseError("json inválido")

        with pytest.raises(ComplianceError):
            raise CompliancePersistenceError("persistência falhou")


class TestComplianceErrorInstantiation:
    """Testes para instanciação e mensagens das exceções."""

    def test_compliance_error_com_mensagem(self):
        """ComplianceError deve armazenar a mensagem corretamente."""
        msg = "erro genérico de compliance"
        exc = ComplianceError(msg)
        assert str(exc) == msg

    def test_analysis_incomplete_com_mensagem(self):
        """AnalysisIncompleteError deve armazenar a mensagem."""
        msg = "Bedrock timeout após 3 tentativas"
        exc = AnalysisIncompleteError(msg)
        assert str(exc) == msg

    def test_compliance_parse_com_mensagem(self):
        """ComplianceParseError deve armazenar a mensagem."""
        msg = "Chave 'compliance_results' ausente na resposta"
        exc = ComplianceParseError(msg)
        assert str(exc) == msg

    def test_compliance_persistence_com_mensagem(self):
        """CompliancePersistenceError deve armazenar a mensagem."""
        msg = "Falha após 3 retries para site_id=abc cycle_id=xyz"
        exc = CompliancePersistenceError(msg)
        assert str(exc) == msg

    def test_excepcoes_sem_mensagem(self):
        """Exceções devem funcionar sem mensagem."""
        exc = ComplianceError()
        assert str(exc) == ""

        exc = AnalysisIncompleteError()
        assert str(exc) == ""

        exc = ComplianceParseError()
        assert str(exc) == ""

        exc = CompliancePersistenceError()
        assert str(exc) == ""


class TestComplianceErrorChaining:
    """Testes para encadeamento de exceções (raise from)."""

    def test_analysis_incomplete_com_causa(self):
        """AnalysisIncompleteError deve suportar encadeamento com __cause__."""
        causa = TimeoutError("connection timed out")
        exc = AnalysisIncompleteError("Bedrock falhou")
        exc.__cause__ = causa

        assert exc.__cause__ is causa

    def test_persistence_error_com_causa(self):
        """CompliancePersistenceError deve suportar encadeamento."""
        causa = ConnectionError("database unavailable")

        try:
            try:
                raise causa
            except ConnectionError as e:
                raise CompliancePersistenceError(
                    "Falha ao persistir após retries"
                ) from e
        except CompliancePersistenceError as exc:
            assert exc.__cause__ is causa
            assert "Falha ao persistir" in str(exc)
