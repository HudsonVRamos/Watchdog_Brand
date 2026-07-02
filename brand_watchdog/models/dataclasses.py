"""Dataclasses de domínio do Brand Watchdog.

Define os DTOs (Data Transfer Objects) utilizados para transferência de dados
entre os componentes do sistema, independentes do ORM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar


# Regras de compliance configuradas para a parceria SKY+/Amazon Prime
COMPLIANCE_RULES: list[str] = [
    "facilitator_role",
    "logo_application",
    "logo_effects",
    "content_separation",
    "naming_pricing",
    "kv_integrity",
]


@dataclass
class BoundingBox:
    """Coordenadas de localização de uma detecção na imagem (em percentual)."""

    x_percent: float
    y_percent: float
    width_percent: float
    height_percent: float


@dataclass
class CaptureResult:
    """Resultado de uma captura de screenshot de um site-alvo."""

    target_url: str
    screenshot_path: Path
    screenshot_ref_id: str
    captured_at: datetime
    page_height_px: int
    was_truncated: bool
    success: bool
    error_message: str | None = None


@dataclass
class DetectionResult:
    """Resultado de detecção de uso de marca em um screenshot."""

    target_url: str
    match_type: str  # "logo" ou "text"
    confidence: int  # 0-100
    bounding_box: BoundingBox
    description: str
    detected_at: datetime
    screenshot_ref_id: str


@dataclass
class BrandAsset:
    """Ativo de marca registrado (logo ou texto)."""

    id: str
    asset_type: str  # "logo" | "text"
    file_path: Path | None
    text_value: str | None
    content_hash: str
    original_filename: str | None
    file_size_bytes: int | None
    created_at: datetime


@dataclass
class TargetSite:
    """Site-alvo registrado para monitoramento."""

    id: str
    url: str
    normalized_url: str
    created_at: datetime
    active: bool
    brand: str = "sky_plus"  # "sky_plus" or "dgo"


@dataclass
class ValidationResult:
    """Resultado de validação de entrada (URL, asset, etc.)."""

    valid: bool
    error: str | None = None


@dataclass
class SiteResult:
    """Resultado do processamento de um site individual em um ciclo."""

    target_url: str
    success: bool
    detections: list[DetectionResult]
    error_message: str | None = None


@dataclass
class CycleResult:
    """Resultado completo de um ciclo de monitoramento."""

    cycle_id: str
    started_at: datetime
    ended_at: datetime
    sites_processed: int
    sites_failed: int
    detections_found: int
    site_results: list[SiteResult]


@dataclass
class QueryResult:
    """Resultado paginado de consulta de detecções."""

    results: list[DetectionResult]
    total_count: int
    page: int
    page_size: int
    has_next: bool


@dataclass
class ComplianceRuleResult:
    """Resultado de validação de uma regra individual de compliance.

    Cada regra da parceria SKY+/Amazon Prime é avaliada individualmente,
    gerando um resultado com status, confiança e descrição dos achados.
    """

    rule_id: str  # ex: "facilitator_role", "logo_application"
    status: str  # "PASS", "FAIL", "NOT_APPLICABLE"
    confidence: int  # 0-100
    description: str  # Descrição dos achados (max 1024 chars)

    _VALID_STATUSES: ClassVar[tuple[str, ...]] = (
        "PASS", "FAIL", "NOT_APPLICABLE",
    )

    def __post_init__(self) -> None:
        """Valida os campos após inicialização."""
        if self.status not in self._VALID_STATUSES:
            raise ValueError(
                f"Status inválido: '{self.status}'. "
                f"Valores aceitos: {self._VALID_STATUSES}"
            )
        if not 0 <= self.confidence <= 100:
            raise ValueError(
                f"Confidence deve ser entre 0 e 100, "
                f"recebido: {self.confidence}"
            )
        if len(self.description) > 1024:
            raise ValueError(
                f"Description excede 1024 caracteres: "
                f"{len(self.description)} chars"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serializa para dicionário."""
        return {
            "rule_id": self.rule_id,
            "status": self.status,
            "confidence": self.confidence,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComplianceRuleResult:
        """Deserializa a partir de dicionário."""
        return cls(
            rule_id=data["rule_id"],
            status=data["status"],
            confidence=data["confidence"],
            description=data["description"],
        )


@dataclass
class ComplianceReport:
    """Relatório consolidado de compliance para um ISP.

    Contém os resultados de todas as regras avaliadas e o status geral
    derivado automaticamente: 'non_compliant' se qualquer regra tem FAIL,
    senão 'compliant'.
    """

    target_url: str
    analyzed_at: datetime
    overall_status: str  # "compliant", "non_compliant", "error"
    rule_results: list[ComplianceRuleResult] = field(default_factory=list)
    screenshot_ref_id: str = ""
    cycle_id: str = ""

    _VALID_OVERALL_STATUSES: ClassVar[tuple[str, ...]] = (
        "compliant",
        "non_compliant",
        "error",
    )

    def __post_init__(self) -> None:
        """Valida os campos após inicialização."""
        if self.overall_status not in self._VALID_OVERALL_STATUSES:
            raise ValueError(
                f"overall_status inválido: '{self.overall_status}'. "
                f"Valores aceitos: {self._VALID_OVERALL_STATUSES}"
            )

    @staticmethod
    def derive_overall_status(rule_results: list[ComplianceRuleResult]) -> str:
        """Deriva o overall_status a partir dos resultados das regras.

        Retorna 'non_compliant' se qualquer regra tem status 'FAIL',
        senão retorna 'compliant'.
        """
        for result in rule_results:
            if result.status == "FAIL":
                return "non_compliant"
        return "compliant"

    def to_dict(self) -> dict[str, Any]:
        """Serializa para dicionário."""
        return {
            "target_url": self.target_url,
            "analyzed_at": self.analyzed_at.isoformat(),
            "overall_status": self.overall_status,
            "rule_results": [r.to_dict() for r in self.rule_results],
            "screenshot_ref_id": self.screenshot_ref_id,
            "cycle_id": self.cycle_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComplianceReport:
        """Deserializa a partir de dicionário."""
        return cls(
            target_url=data["target_url"],
            analyzed_at=datetime.fromisoformat(data["analyzed_at"]),
            overall_status=data["overall_status"],
            rule_results=[
                ComplianceRuleResult.from_dict(r) for r in data["rule_results"]
            ],
            screenshot_ref_id=data["screenshot_ref_id"],
            cycle_id=data["cycle_id"],
        )
