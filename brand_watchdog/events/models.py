"""Dataclasses de eventos do EventBridge.

Define os contratos de dados para eventos publicados e consumidos
via AWS EventBridge.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class ComplianceCompletedEvent:
    """Evento publicado após a conclusão da análise de compliance de um site.

    Publicado no EventBridge com source="brand-watchdog" e
    detail-type="ComplianceCompleted".

    Attributes:
        site_id: UUID do site-alvo analisado.
        cycle_id: UUID do ciclo de monitoramento.
        target_url: URL do site-alvo (máximo 2048 caracteres).
        brand: Marca do site ("sky_plus" ou "dgo").
        overall_status: Status geral da análise ("compliant" ou "non_compliant").
        rule_results: Lista de resultados por regra, cada um com rule_id, status e confidence.
        screenshot_s3_key: Chave S3 do screenshot analisado.
        analyzed_at: Timestamp UTC da análise em formato ISO 8601.
    """

    site_id: str
    cycle_id: str
    target_url: str
    brand: str
    overall_status: str
    rule_results: list[dict] = field(default_factory=list)
    screenshot_s3_key: str = ""
    analyzed_at: str = ""

    def to_event_detail(self) -> dict:
        """Converte o evento para o formato do campo 'detail' do EventBridge.

        Returns:
            Dicionário com todos os campos do evento para uso em PutEvents.
        """
        return asdict(self)

    def to_json(self) -> str:
        """Serializa o evento para JSON.

        Returns:
            String JSON contendo todos os campos do evento.
        """
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> ComplianceCompletedEvent:
        """Deserializa um evento a partir de JSON.

        Args:
            json_str: String JSON com os campos do evento.

        Returns:
            Instância de ComplianceCompletedEvent.

        Raises:
            ValueError: Se campos obrigatórios estiverem ausentes.
            json.JSONDecodeError: Se a string não for um JSON válido.
        """
        data = json.loads(json_str)
        required_fields = {
            "site_id", "cycle_id", "target_url", "brand",
            "overall_status", "rule_results", "screenshot_s3_key", "analyzed_at",
        }
        missing = required_fields - set(data.keys())
        if missing:
            raise ValueError(
                f"Campos obrigatórios ausentes no evento: {', '.join(sorted(missing))}"
            )
        return cls(
            site_id=str(data["site_id"]),
            cycle_id=str(data["cycle_id"]),
            target_url=str(data["target_url"]),
            brand=str(data["brand"]),
            overall_status=str(data["overall_status"]),
            rule_results=data["rule_results"],
            screenshot_s3_key=str(data["screenshot_s3_key"]),
            analyzed_at=str(data["analyzed_at"]),
        )
