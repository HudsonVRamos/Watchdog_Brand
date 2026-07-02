"""Hierarquia de exceções para o módulo de compliance.

Define exceções específicas para erros de análise de compliance,
parsing de respostas e persistência de resultados.

Requisitos cobertos: 7.6, 9.4
"""

from __future__ import annotations


class ComplianceError(Exception):
    """Erro base para o módulo de compliance.

    Todas as exceções relacionadas ao fluxo de validação de compliance
    herdam desta classe, permitindo captura genérica quando necessário.
    """

    pass


class AnalysisIncompleteError(ComplianceError):
    """Análise não pôde ser concluída (Bedrock falhou ou screenshot ilegível).

    Levantada quando:
    - O screenshot não pode ser lido do disco.
    - O Bedrock Client falha após todas as tentativas de retry.
    - A imagem está corrompida ou ilegível para análise.
    """

    pass


class ComplianceParseError(ComplianceError):
    """Resposta do Bedrock não pôde ser parseada em ComplianceReport válido.

    Levantada quando:
    - A resposta JSON não contém a chave 'compliance_results'.
    - Valores de status são inválidos (não são PASS/FAIL/NOT_APPLICABLE).
    - Campos obrigatórios estão ausentes na resposta.
    - A resposta contém menos regras que o conjunto configurado.
    """

    pass


class CompliancePersistenceError(ComplianceError):
    """Falha ao persistir resultados de compliance após retries.

    Levantada quando:
    - Todas as tentativas de persistência (3 retries com backoff
      exponencial) foram exauridas sem sucesso.
    """

    pass
