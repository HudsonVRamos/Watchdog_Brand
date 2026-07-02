"""Testes unitários para o Configuration Manager."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from brand_watchdog.config import (
    AlertConfig,
    AnalyzerConfig,
    AppConfig,
    CrawlerConfig,
    ScheduleConfig,
    StorageConfig,
    load_config,
)


class TestDefaultConfig:
    """Testa que a configuração padrão é criada corretamente."""

    def test_load_config_sem_yaml_retorna_defaults(self):
        config = load_config()
        assert isinstance(config, AppConfig)
        assert config.schedule.interval_hours == 24
        assert config.storage.screenshot_retention_days == 90
        assert config.storage.detection_retention_days == 90
        assert config.max_target_sites == 200

    def test_crawler_config_defaults(self):
        config = load_config()
        assert config.crawler.viewport_width == 1280
        assert config.crawler.page_timeout_seconds == 60
        assert config.crawler.network_idle_timeout_ms == 500
        assert config.crawler.max_screenshot_height_px == 20000
        assert config.crawler.screenshot_format == "png"

    def test_analyzer_config_defaults(self):
        config = load_config()
        assert config.analyzer.bedrock_model_id == "anthropic.claude-sonnet-4-6"
        assert config.analyzer.bedrock_region == "us-east-1"
        assert config.analyzer.confidence_threshold == 70
        assert config.analyzer.request_timeout_seconds == 60
        assert config.analyzer.max_retries == 3
        assert config.analyzer.retry_base_delay_seconds == 2.0

    def test_alert_config_defaults(self):
        config = load_config()
        assert config.alert.provider == "ses"
        assert config.alert.ses_region == "us-east-1"
        assert config.alert.smtp_port == 587
        assert config.alert.recipients == []
        assert config.alert.retry_attempts == 3
        assert config.alert.retry_interval_seconds == 30

    def test_storage_config_defaults(self):
        config = load_config()
        assert config.storage.screenshot_base_path == Path("./data/screenshots")
        assert "sqlite" in config.storage.database_url


class TestYamlLoading:
    """Testa carregamento de configuração via YAML."""

    def test_load_config_de_yaml(self, tmp_path):
        yaml_content = """
schedule:
  interval_hours: 12

storage:
  screenshot_retention_days: 30
  detection_retention_days: 60

crawler:
  viewport_width: 1920
  page_timeout_seconds: 90

max_target_sites: 100
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        config = load_config(yaml_file)

        assert config.schedule.interval_hours == 12
        assert config.storage.screenshot_retention_days == 30
        assert config.storage.detection_retention_days == 60
        assert config.crawler.viewport_width == 1920
        assert config.crawler.page_timeout_seconds == 90
        assert config.max_target_sites == 100

    def test_load_config_yaml_parcial_mantem_defaults(self, tmp_path):
        yaml_content = """
schedule:
  interval_hours: 6
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        config = load_config(yaml_file)

        assert config.schedule.interval_hours == 6
        # Demais valores permanecem como default
        assert config.crawler.viewport_width == 1280
        assert config.storage.screenshot_retention_days == 90

    def test_load_config_yaml_inexistente_levanta_erro(self):
        with pytest.raises(FileNotFoundError):
            load_config(Path("/caminho/inexistente/config.yaml"))

    def test_load_config_yaml_vazio(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("", encoding="utf-8")

        config = load_config(yaml_file)
        assert config.schedule.interval_hours == 24

    def test_load_config_yaml_com_alert_recipients(self, tmp_path):
        yaml_content = """
alert:
  provider: smtp
  smtp_host: smtp.example.com
  smtp_port: 465
  recipients:
    - admin@example.com
    - security@example.com
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        config = load_config(yaml_file)

        assert config.alert.provider == "smtp"
        assert config.alert.smtp_host == "smtp.example.com"
        assert config.alert.smtp_port == 465
        assert config.alert.recipients == ["admin@example.com", "security@example.com"]


class TestEnvOverrides:
    """Testa override de configuração por variáveis de ambiente."""

    def test_env_override_schedule_interval(self):
        env = {"BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS": "48"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.schedule.interval_hours == 48

    def test_env_override_storage_retention(self):
        env = {
            "BRAND_WATCHDOG_STORAGE_SCREENSHOT_RETENTION_DAYS": "180",
            "BRAND_WATCHDOG_STORAGE_DETECTION_RETENTION_DAYS": "120",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.storage.screenshot_retention_days == 180
            assert config.storage.detection_retention_days == 120

    def test_env_override_crawler_viewport(self):
        env = {"BRAND_WATCHDOG_CRAWLER_VIEWPORT_WIDTH": "1920"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.crawler.viewport_width == 1920

    def test_env_override_max_target_sites(self):
        env = {"BRAND_WATCHDOG_MAX_TARGET_SITES": "50"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.max_target_sites == 50

    def test_env_override_sobrescreve_yaml(self, tmp_path):
        yaml_content = """
schedule:
  interval_hours: 12
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        env = {"BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS": "48"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config(yaml_file)
            # Env var tem prioridade sobre YAML
            assert config.schedule.interval_hours == 48

    def test_env_override_alert_recipients_por_virgula(self):
        env = {"BRAND_WATCHDOG_ALERT_RECIPIENTS": "a@test.com,b@test.com"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.alert.recipients == ["a@test.com", "b@test.com"]

    def test_env_override_analyzer_retry_base_delay(self):
        env = {"BRAND_WATCHDOG_ANALYZER_RETRY_BASE_DELAY_SECONDS": "5.0"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.analyzer.retry_base_delay_seconds == 5.0


class TestValidation:
    """Testa validação de valores de configuração."""

    def test_interval_hours_abaixo_do_minimo_levanta_erro(self, tmp_path):
        yaml_content = """
schedule:
  interval_hours: 0
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="schedule.interval_hours"):
            load_config(yaml_file)

    def test_interval_hours_acima_do_maximo_levanta_erro(self, tmp_path):
        yaml_content = """
schedule:
  interval_hours: 721
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="schedule.interval_hours"):
            load_config(yaml_file)

    def test_interval_hours_negativo_levanta_erro(self):
        env = {"BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS": "-5"}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="schedule.interval_hours"):
                load_config()

    def test_screenshot_retention_abaixo_do_minimo_levanta_erro(self, tmp_path):
        yaml_content = """
storage:
  screenshot_retention_days: 0
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="screenshot_retention_days"):
            load_config(yaml_file)

    def test_screenshot_retention_acima_do_maximo_levanta_erro(self, tmp_path):
        yaml_content = """
storage:
  screenshot_retention_days: 366
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="screenshot_retention_days"):
            load_config(yaml_file)

    def test_detection_retention_abaixo_do_minimo_levanta_erro(self, tmp_path):
        yaml_content = """
storage:
  detection_retention_days: 0
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="detection_retention_days"):
            load_config(yaml_file)

    def test_detection_retention_acima_do_maximo_levanta_erro(self, tmp_path):
        yaml_content = """
storage:
  detection_retention_days: 400
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        with pytest.raises(ValueError, match="detection_retention_days"):
            load_config(yaml_file)

    def test_valores_limite_validos(self):
        """Testa que os valores nos limites são aceitos."""
        env = {
            "BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS": "1",
            "BRAND_WATCHDOG_STORAGE_SCREENSHOT_RETENTION_DAYS": "365",
            "BRAND_WATCHDOG_STORAGE_DETECTION_RETENTION_DAYS": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.schedule.interval_hours == 1
            assert config.storage.screenshot_retention_days == 365
            assert config.storage.detection_retention_days == 1

    def test_valores_limite_maximo_validos(self):
        """Testa que os valores máximos nos limites são aceitos."""
        env = {
            "BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS": "720",
            "BRAND_WATCHDOG_STORAGE_SCREENSHOT_RETENTION_DAYS": "1",
            "BRAND_WATCHDOG_STORAGE_DETECTION_RETENTION_DAYS": "365",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.schedule.interval_hours == 720
            assert config.storage.screenshot_retention_days == 1
            assert config.storage.detection_retention_days == 365
