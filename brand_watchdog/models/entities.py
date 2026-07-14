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
    UniqueConstraint,
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
    site_cycle_results = relationship(
        "SiteCycleResultModel",
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
    Status válidos: "running", "dispatched", "completed",
                    "completed_with_timeout", "skipped", "error"
    """

    __tablename__ = "monitoring_cycles"

    # Status válidos para o ciclo
    STATUS_RUNNING = "running"
    STATUS_DISPATCHED = "dispatched"
    STATUS_COMPLETED = "completed"
    STATUS_COMPLETED_WITH_TIMEOUT = "completed_with_timeout"
    STATUS_SKIPPED = "skipped"
    STATUS_ERROR = "error"

    VALID_STATUSES = (
        STATUS_RUNNING,
        STATUS_DISPATCHED,
        STATUS_COMPLETED,
        STATUS_COMPLETED_WITH_TIMEOUT,
        STATUS_SKIPPED,
        STATUS_ERROR,
    )

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    started_at: datetime = Column(DateTime(timezone=True), nullable=False)
    ended_at: datetime | None = Column(DateTime(timezone=True), nullable=True)
    sites_processed: int = Column(Integer, default=0)
    sites_failed: int = Column(Integer, default=0)
    sites_dispatched: int = Column(Integer, default=0)
    detections_found: int = Column(Integer, default=0)
    status: str = Column(String(20), default="running")
    rule_set_version: str | None = Column(String(30), nullable=True)

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
    site_cycle_results = relationship(
        "SiteCycleResultModel",
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
    O campo s3_key armazena a chave do objeto no S3
    (formato: screenshots/{cycle_id}/{screenshot_id}.png).
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
    s3_key: str = Column(String(512), nullable=False)
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


class SiteCycleResultModel(Base):
    """Modelo para resultados de processamento por site dentro de um ciclo.

    Registra o resultado (sucesso ou falha) do processamento de cada site
    individual por um Worker ECS. Usado para consolidação do ciclo.
    """

    __tablename__ = "site_cycle_results"
    __table_args__ = (
        UniqueConstraint("site_id", "cycle_id", name="uq_site_cycle"),
        Index("ix_site_cycle_results_cycle_id", "cycle_id"),
    )

    # Status válidos para resultado do site
    STATUS_SUCCESS = "success"
    STATUS_FAILURE = "failure"

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    site_id: str = Column(
        String, ForeignKey("target_sites.id"), nullable=False
    )
    cycle_id: str = Column(
        String, ForeignKey("monitoring_cycles.id"), nullable=False
    )
    status: str = Column(String(20), nullable=False)
    detections_count: int = Column(Integer, default=0)
    failure_reason: str | None = Column(String(1024), nullable=True)
    completed_at: datetime = Column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    target_site = relationship(
        "TargetSiteModel", back_populates="site_cycle_results"
    )
    monitoring_cycle = relationship(
        "MonitoringCycleModel", back_populates="site_cycle_results"
    )

    def __repr__(self) -> str:
        return (
            f"<SiteCycleResultModel(id={self.id!r}, "
            f"site_id={self.site_id!r}, status={self.status!r})>"
        )


class NotificationDedupModel(Base):
    """Modelo para deduplicação de notificações por email.

    Garante que cada combinação (cycle_id, target_url) só é processada
    uma vez, evitando reenvio de emails duplicados.
    """

    __tablename__ = "notification_dedup"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id", "target_url", name="uq_cycle_url"
        ),
        Index(
            "ix_notification_dedup_cycle_url",
            "cycle_id",
            "target_url",
        ),
    )

    id: str = Column(String, primary_key=True, default=_generate_uuid)
    cycle_id: str = Column(String, nullable=False)
    target_url: str = Column(String(2048), nullable=False)
    processed_at: datetime = Column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationDedupModel(id={self.id!r}, "
            f"cycle_id={self.cycle_id!r}, "
            f"target_url={self.target_url!r})>"
        )
