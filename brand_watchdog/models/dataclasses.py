"""Dataclasses de domínio do Brand Watchdog.

Define os DTOs (Data Transfer Objects) utilizados para transferência de dados
entre os componentes do sistema, independentes do ORM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


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
