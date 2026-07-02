"""Gerenciamento de configuração do Brand Watchdog.

Carrega configuração a partir de arquivo YAML com override por variáveis de ambiente.
Padrão de variáveis de ambiente: BRAND_WATCHDOG_<SECTION>_<FIELD>
Exemplo: BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS=12
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CrawlerConfig:
    """Configuração do crawler Playwright."""

    viewport_width: int = 1280
    page_timeout_seconds: int = 60
    network_idle_timeout_ms: int = 500
    max_screenshot_height_px: int = 20000
    screenshot_format: str = "png"


@dataclass
class AnalyzerConfig:
    """Configuração do analisador AWS Bedrock."""

    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-6"
    bedrock_region: str = "us-east-1"
    confidence_threshold: int = 70
    request_timeout_seconds: int = 60
    max_retries: int = 3
    retry_base_delay_seconds: float = 2.0


@dataclass
class AlertConfig:
    """Configuração do serviço de alertas por email."""

    provider: str = "ses"  # "ses" ou "smtp"
    ses_region: str = "us-east-1"
    ses_sender: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    recipients: list[str] = field(default_factory=list)
    retry_attempts: int = 3
    retry_interval_seconds: int = 30


@dataclass
class ScheduleConfig:
    """Configuração de agendamento de ciclos de monitoramento."""

    interval_hours: int = 24  # 1 a 720


@dataclass
class StorageConfig:
    """Configuração de armazenamento de screenshots e detecções."""

    screenshot_retention_days: int = 90
    detection_retention_days: int = 90
    screenshot_base_path: Path = field(default_factory=lambda: Path("./data/screenshots"))
    database_url: str = "sqlite+aiosqlite:///./data/brand_watchdog.db"
    s3_bucket: str = "brand-watchdog-screenshots-761018874615"
    s3_region: str = "us-east-1"
    s3_multipart_threshold: int = 5_242_880  # 5MB


@dataclass
class QueueConfig:
    """Configuração da fila SQS."""

    queue_url: str = ""
    dlq_url: str = ""
    visibility_timeout_seconds: int = 120
    max_receive_count: int = 3
    batch_size: int = 10
    publish_timeout_minutes: int = 5


@dataclass
class EventConfig:
    """Configuração do EventBridge."""

    event_bus_name: str = "default"
    source: str = "brand-watchdog"
    detail_type_compliance: str = "ComplianceCompleted"
    region: str = "us-east-1"
    max_retries: int = 3


@dataclass
class WorkerConfig:
    """Configuração do Worker ECS."""

    processing_timeout_seconds: int = 120
    visibility_renew_interval_seconds: int = 60
    consolidation_poll_interval_seconds: int = 30
    consolidation_timeout_minutes: int = 60
    max_concurrent_tasks: int = 10
    scale_target_messages_per_task: int = 5
    scale_in_cooldown_seconds: int = 120
    scale_out_cooldown_seconds: int = 60


@dataclass
class CacheConfig:
    """Configuração do cache de referências."""

    max_image_size_px: int = 1568
    jpeg_quality: int = 85
    enable_prompt_caching: bool = True


# Valid brand types for compliance monitoring
BRAND_TYPES = ("sky_plus", "dgo")


@dataclass
class AppConfig:
    """Configuração raiz da aplicação, agrega todas as sub-configurações."""

    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    event: EventConfig = field(default_factory=EventConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    max_target_sites: int = 200
    brand: str = "sky_plus"  # "sky_plus" or "dgo"


# Mapeamento de nomes de seção para suas dataclasses
_SECTION_MAP: dict[str, type] = {
    "crawler": CrawlerConfig,
    "analyzer": AnalyzerConfig,
    "alert": AlertConfig,
    "schedule": ScheduleConfig,
    "storage": StorageConfig,
    "queue": QueueConfig,
    "event": EventConfig,
    "worker": WorkerConfig,
    "cache": CacheConfig,
}


def _coerce_value(field_type: type, value: Any) -> Any:
    """Converte valor string (de env var) para o tipo correto do campo."""
    if field_type is bool:
        return value.lower() in ("true", "1", "yes") if isinstance(value, str) else bool(value)
    if field_type is int:
        return int(value)
    if field_type is float:
        return float(value)
    if field_type is Path:
        return Path(value)
    if field_type is str:
        return str(value)
    # Para list[str], aceita valores separados por vírgula
    if field_type is list or (hasattr(field_type, "__origin__") and getattr(field_type, "__origin__", None) is list):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
    return value


def _get_field_type(dataclass_type: type, field_name: str) -> type:
    """Obtém o tipo de um campo específico de uma dataclass.

    Usa get_type_hints para resolver anotações de tipo corretamente,
    mesmo com 'from __future__ import annotations'.
    """
    import typing

    hints = typing.get_type_hints(dataclass_type)
    if field_name in hints:
        return hints[field_name]
    raise ValueError(f"Campo '{field_name}' não encontrado em {dataclass_type.__name__}")


def _apply_env_overrides(config: AppConfig) -> None:
    """Aplica overrides de variáveis de ambiente sobre a configuração.

    Padrão: BRAND_WATCHDOG_<SECTION>_<FIELD> (tudo em uppercase).
    Exemplo: BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS=12
    """
    prefix = "BRAND_WATCHDOG_"

    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue

        # Remove o prefixo e converte para lowercase
        remainder = env_key[len(prefix):].lower()

        # Tenta encontrar a seção correspondente
        matched_section = None
        matched_field = None

        for section_name in _SECTION_MAP:
            section_prefix = f"{section_name}_"
            if remainder.startswith(section_prefix):
                potential_field = remainder[len(section_prefix):]
                # Verifica se o campo existe na seção
                section_cls = _SECTION_MAP[section_name]
                section_fields = {f.name for f in fields(section_cls)}
                if potential_field in section_fields:
                    matched_section = section_name
                    matched_field = potential_field
                    break

        # Verifica campos de nível raiz (ex: max_target_sites)
        if matched_section is None:
            root_fields = {f.name for f in fields(AppConfig) if f.name not in _SECTION_MAP}
            if remainder in root_fields:
                field_type = _get_field_type(AppConfig, remainder)
                setattr(config, remainder, _coerce_value(field_type, env_value))
                continue

        if matched_section and matched_field:
            section_obj = getattr(config, matched_section)
            section_cls = _SECTION_MAP[matched_section]
            field_type = _get_field_type(section_cls, matched_field)
            setattr(section_obj, matched_field, _coerce_value(field_type, env_value))


def _validate_config(config: AppConfig) -> None:
    """Valida os valores de configuração e levanta ValueError se inválidos."""
    # Valida interval_hours: deve estar entre 1 e 720
    if not (1 <= config.schedule.interval_hours <= 720):
        raise ValueError(
            f"schedule.interval_hours deve estar entre 1 e 720, "
            f"valor recebido: {config.schedule.interval_hours}"
        )

    # Valida screenshot_retention_days: deve estar entre 1 e 365
    if not (1 <= config.storage.screenshot_retention_days <= 365):
        raise ValueError(
            f"storage.screenshot_retention_days deve estar entre 1 e 365, "
            f"valor recebido: {config.storage.screenshot_retention_days}"
        )

    # Valida detection_retention_days: deve estar entre 1 e 365
    if not (1 <= config.storage.detection_retention_days <= 365):
        raise ValueError(
            f"storage.detection_retention_days deve estar entre 1 e 365, "
            f"valor recebido: {config.storage.detection_retention_days}"
        )


def _build_config_from_dict(data: dict[str, Any]) -> AppConfig:
    """Constrói AppConfig a partir de um dicionário (normalmente vindo do YAML)."""
    kwargs: dict[str, Any] = {}

    for section_name, section_cls in _SECTION_MAP.items():
        section_data = data.get(section_name, {})
        if not isinstance(section_data, dict):
            section_data = {}

        # Filtra apenas campos válidos da dataclass
        valid_fields = {f.name for f in fields(section_cls)}
        filtered_data: dict[str, Any] = {}
        for key, value in section_data.items():
            if key in valid_fields:
                field_type = _get_field_type(section_cls, key)
                filtered_data[key] = _coerce_value(field_type, value)

        kwargs[section_name] = section_cls(**filtered_data)

    # Campos de nível raiz
    root_fields = {f.name for f in fields(AppConfig) if f.name not in _SECTION_MAP}
    for field_name in root_fields:
        if field_name in data:
            field_type = _get_field_type(AppConfig, field_name)
            kwargs[field_name] = _coerce_value(field_type, data[field_name])

    return AppConfig(**kwargs)


def load_config(yaml_path: Path | None = None) -> AppConfig:
    """Carrega configuração do sistema.

    1. Cria configuração com valores padrão
    2. Se yaml_path fornecido e existente, aplica valores do YAML
    3. Aplica overrides de variáveis de ambiente (BRAND_WATCHDOG_*)
    4. Valida a configuração final

    Args:
        yaml_path: Caminho opcional para arquivo YAML de configuração.

    Returns:
        AppConfig com a configuração final validada.

    Raises:
        ValueError: Se algum valor de configuração for inválido.
        FileNotFoundError: Se yaml_path fornecido não existir.
    """
    if yaml_path is not None and not yaml_path.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {yaml_path}")

    # Carrega do YAML ou usa defaults
    if yaml_path is not None:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = _build_config_from_dict(data)
    else:
        config = AppConfig()

    # Aplica overrides de variáveis de ambiente
    _apply_env_overrides(config)

    # Valida configuração final
    _validate_config(config)

    return config
