"""Entidades SQLAlchemy do Brand Watchdog.

Define os modelos ORM para persistência de dados do sistema:
- TargetSiteModel: Sites monitorados
- BrandAssetModel: Ativos de marca (logos e textos)
- MonitoringCycleModel: Ciclos de monitoramento
- ScreenshotModel: Screenshots capturados
- DetectionResultModel: Resultados de detecção
- AlertLogModel: Logs de alertas enviados
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, relationship

import uuid


class Base(DeclarativeBase):
    """Classe base declarativa para todos os modelos."""

    pass


def _generate_uuid() -> str:
    """Gera um UUID v4 como string."""
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    """Retorna datetime UTC atual."""
    return datetime.now(timezone.utc)


class TargetSiteModel(Base):
    """Modelo para sites-alvo de monitoramento.

    Armazena URLs configuradas para monitoramento periódico.
    A URL normalizada é usada para deduplicação (unique).
    """

    __tablename__ = "target_sites"

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    url: str = Column(String(2048), nullable=False)
    normalized_url: str = Column(String(2048), nullable=False, unique=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_utcnow)
    active: bool = Column(Boolean, default=True)
    brand: str = Column(String(20), nullable=False, default="sky_plus")

    # Relationships
    screenshots = relationship(
        "ScreenshotModel",
        back_populates="target_site",
        cascade="all, delete-orphan",
    )
    detection_results = relationship(
        "DetectionResultModel",
        back_populates="target_site",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<TargetSiteModel(id={self.id!r}, "
            f"url={self.url!r}, active={self.active})>"
        )


class BrandAssetModel(Base):
    """Modelo para ativos de marca registrados.

    Armazena logos (PNG, JPG, SVG) e textos de marca.
    O content_hash garante deduplicação (unique).
    """

    __tablename__ = "brand_assets"

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    asset_type: str = Column(String(10), nullable=False)  # "logo" | "text"
    file_path: str | None = Column(String(512), nullable=True)
    text_value: str | None = Column(String(256), nullable=True)
    content_hash: str = Column(String(64), nullable=False, unique=True)
    original_filename: str | None = Column(String(256), nullable=True)
    file_size_bytes: int | None = Column(Integer, nullable=True)
    created_at: datetime = Column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:
        return (
            f"<BrandAssetModel(id={self.id!r}, type={self.asset_type!r}, "
            f"hash={self.content_hash!r})>"
        )


class MonitoringCycleModel(Base):
    """Modelo para ciclos de monitoramento.

    Registra cada execução do ciclo com estatísticas de processamento.
    """

    __tablename__ = "monitoring_cycles"

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    started_at: datetime = Column(DateTime(timezone=True), nullable=False)
    ended_at: datetime | None = Column(DateTime(timezone=True), nullable=True)
    sites_processed: int = Column(Integer, default=0)
    sites_failed: int = Column(Integer, default=0)
    detections_found: int = Column(Integer, default=0)
    status: str = Column(String(20), default="running")

    # Relationships
    screenshots = relationship(
        "ScreenshotModel",
        back_populates="monitoring_cycle",
        cascade="all, delete-orphan",
    )
    detection_results = relationship(
        "DetectionResultModel",
        back_populates="monitoring_cycle",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<MonitoringCycleModel(id={self.id!r}, status={self.status!r}, "
            f"processed={self.sites_processed})>"
        )


class ScreenshotModel(Base):
    """Modelo para screenshots capturados.

    Armazena metadados de cada screenshot com referência ao site e ciclo.
    O campo expires_at é indexado para queries de cleanup eficientes.
    """

    __tablename__ = "screenshots"
    __table_args__ = (
        Index("ix_screenshots_expires_at", "expires_at"),
    )

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    target_site_id: str = Column(
        String, ForeignKey("target_sites.id"), nullable=False
    )
    monitoring_cycle_id: str = Column(
        String, ForeignKey("monitoring_cycles.id"), nullable=False
    )
    file_path: str = Column(String(512), nullable=False)
    captured_at: datetime = Column(DateTime(timezone=True), nullable=False)
    height_px: int = Column(Integer, nullable=False)
    was_truncated: bool = Column(Boolean, default=False)
    expires_at: datetime = Column(DateTime(timezone=True), nullable=False)

    # Relationships
    target_site = relationship("TargetSiteModel", back_populates="screenshots")
    monitoring_cycle = relationship(
        "MonitoringCycleModel", back_populates="screenshots"
    )
    detection_results = relationship(
        "DetectionResultModel",
        back_populates="screenshot",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<ScreenshotModel(id={self.id!r}, site={self.target_site_id!r}, "
            f"captured_at={self.captured_at})>"
        )


class DetectionResultModel(Base):
    """Modelo para resultados de detecção de marca.

    Armazena cada detecção com coordenadas de bounding box (percentuais),
    tipo de match e nível de confiança.
    O campo expires_at é indexado para queries de cleanup eficientes.
    """

    __tablename__ = "detection_results"
    __table_args__ = (
        Index("ix_detection_results_expires_at", "expires_at"),
    )

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    target_site_id: str = Column(
        String, ForeignKey("target_sites.id"), nullable=False
    )
    screenshot_id: str = Column(
        String, ForeignKey("screenshots.id"), nullable=False
    )
    monitoring_cycle_id: str = Column(
        String, ForeignKey("monitoring_cycles.id"), nullable=False
    )
    match_type: str = Column(String(10), nullable=False)  # "logo" | "text"
    confidence: int = Column(Integer, nullable=False)  # 0-100
    bbox_x_percent: float = Column(Float, nullable=False)
    bbox_y_percent: float = Column(Float, nullable=False)
    bbox_width_percent: float = Column(Float, nullable=False)
    bbox_height_percent: float = Column(Float, nullable=False)
    description: str = Column(String(1024), nullable=False)
    detected_at: datetime = Column(DateTime(timezone=True), nullable=False)
    expires_at: datetime = Column(DateTime(timezone=True), nullable=False)

    # Relationships
    target_site = relationship(
        "TargetSiteModel", back_populates="detection_results"
    )
    screenshot = relationship(
        "ScreenshotModel", back_populates="detection_results"
    )
    monitoring_cycle = relationship(
        "MonitoringCycleModel", back_populates="detection_results"
    )
    alert_logs = relationship(
        "AlertLogModel",
        back_populates="detection_result",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<DetectionResultModel(id={self.id!r}, type={self.match_type!r}, "
            f"confidence={self.confidence})>"
        )


class AlertLogModel(Base):
    """Modelo para logs de alertas enviados.

    Registra cada tentativa de envio de alerta com status de sucesso/falha.
    """

    __tablename__ = "alert_logs"

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    detection_result_id: str = Column(
        String, ForeignKey("detection_results.id"), nullable=False
    )
    recipient: str = Column(String(256), nullable=False)
    sent_at: datetime = Column(DateTime(timezone=True), nullable=False)
    success: bool = Column(Boolean, nullable=False)
    error_message: str | None = Column(String(1024), nullable=True)

    # Relationships
    detection_result = relationship(
        "DetectionResultModel", back_populates="alert_logs"
    )

    def __repr__(self) -> str:
        return (
            f"<AlertLogModel(id={self.id!r}, recipient={self.recipient!r}, "
            f"success={self.success})>"
        )
