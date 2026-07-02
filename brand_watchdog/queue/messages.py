"""Dataclasses de mensagens da fila SQS.

Define os contratos de dados para mensagens trocadas via SQS
entre o Coordinator e os Workers ECS.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class ProcessingMessage:
    """Mensagem de processamento publicada na fila SQS.

    Contém todos os dados necessários para um Worker processar um site.

    Attributes:
        site_id: UUID do site-alvo.
        cycle_id: UUID do ciclo de monitoramento.
        brand: Marca do site ("sky_plus" ou "dgo").
        url: URL do site-alvo (máximo 2048 caracteres).
        rule_set_version: Versão do conjunto de regras no formato "v{timestamp}_{hash_8}".
    """

    site_id: str
    cycle_id: str
    brand: str
    url: str
    rule_set_version: str

    def to_json(self) -> str:
        """Serializa a mensagem para JSON.

        Returns:
            String JSON contendo todos os campos obrigatórios.
        """
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> ProcessingMessage:
        """Deserializa uma mensagem a partir de JSON.

        Args:
            json_str: String JSON com os campos da mensagem.

        Returns:
            Instância de ProcessingMessage com os dados deserializados.

        Raises:
            ValueError: Se o JSON for inválido ou campos obrigatórios estiverem ausentes.
            json.JSONDecodeError: Se a string não for um JSON válido.
        """
        data = json.loads(json_str)
        required_fields = {"site_id", "cycle_id", "brand", "url", "rule_set_version"}
        missing = required_fields - set(data.keys())
        if missing:
            raise ValueError(
                f"Campos obrigatórios ausentes na mensagem: {', '.join(sorted(missing))}"
            )
        return cls(
            site_id=str(data["site_id"]),
            cycle_id=str(data["cycle_id"]),
            brand=str(data["brand"]),
            url=str(data["url"]),
            rule_set_version=str(data["rule_set_version"]),
        )
