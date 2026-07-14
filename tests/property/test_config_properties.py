"""Property tests para validação de configuração do Brand Watchdog.

Validates: Requirements 5.2, 7.3, 8.3
"""

import os

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.config import load_config


# Suprime o health check de fixture function-scoped pois
# monkeypatch apenas seta variáveis de ambiente que não mantêm estado
# entre iterações — cada chamada de load_config lê o env fresco.
_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove variáveis de ambiente BRAND_WATCHDOG_* antes de cada
    teste."""
    keys_to_remove = [
        k for k in os.environ if k.startswith("BRAND_WATCHDOG_")
    ]
    for key in keys_to_remove:
        monkeypatch.delenv(key, raising=False)


class TestScheduleFrequencyValidation:
    """Property 10: Schedule Frequency Validation.

    Inteiros de -100 a 1000, aceita apenas [1, 720].

    **Validates: Requirements 5.2**
    """

    @_PBT_SETTINGS
    @given(interval=st.integers(min_value=1, max_value=720))
    def test_valid_interval_hours_accepted(
        self, interval, monkeypatch
    ):
        """Valores válidos de interval_hours [1, 720] devem
        carregar com sucesso."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS",
            str(interval),
        )
        config = load_config()
        assert config.schedule.interval_hours == interval

    @_PBT_SETTINGS
    @given(interval=st.integers(min_value=-100, max_value=0))
    def test_interval_hours_below_minimum_rejected(
        self, interval, monkeypatch
    ):
        """Valores abaixo de 1 devem levantar ValueError."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS",
            str(interval),
        )
        with pytest.raises(ValueError, match="interval_hours"):
            load_config()

    @_PBT_SETTINGS
    @given(interval=st.integers(min_value=721, max_value=1000))
    def test_interval_hours_above_maximum_rejected(
        self, interval, monkeypatch
    ):
        """Valores acima de 720 devem levantar ValueError."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS",
            str(interval),
        )
        with pytest.raises(ValueError, match="interval_hours"):
            load_config()


class TestRetentionPeriodConfigurationValidation:
    """Property 16: Retention Period Configuration Validation.

    Inteiros de -100 a 500, aceita apenas [1, 365].

    **Validates: Requirements 7.3, 8.3**
    """

    @_PBT_SETTINGS
    @given(days=st.integers(min_value=1, max_value=365))
    def test_valid_screenshot_retention_days_accepted(
        self, days, monkeypatch
    ):
        """Valores válidos de screenshot_retention_days [1, 365]
        devem carregar com sucesso."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_STORAGE_SCREENSHOT_RETENTION_DAYS",
            str(days),
        )
        config = load_config()
        assert config.storage.screenshot_retention_days == days

    @_PBT_SETTINGS
    @given(days=st.integers(min_value=-100, max_value=0))
    def test_screenshot_retention_days_below_minimum_rejected(
        self, days, monkeypatch
    ):
        """Valores abaixo de 1 devem levantar ValueError."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_STORAGE_SCREENSHOT_RETENTION_DAYS",
            str(days),
        )
        with pytest.raises(
            ValueError, match="screenshot_retention_days"
        ):
            load_config()

    @_PBT_SETTINGS
    @given(days=st.integers(min_value=366, max_value=500))
    def test_screenshot_retention_days_above_maximum_rejected(
        self, days, monkeypatch
    ):
        """Valores acima de 365 devem levantar ValueError."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_STORAGE_SCREENSHOT_RETENTION_DAYS",
            str(days),
        )
        with pytest.raises(
            ValueError, match="screenshot_retention_days"
        ):
            load_config()

    @_PBT_SETTINGS
    @given(days=st.integers(min_value=1, max_value=365))
    def test_valid_detection_retention_days_accepted(
        self, days, monkeypatch
    ):
        """Valores válidos de detection_retention_days [1, 365]
        devem carregar com sucesso."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_STORAGE_DETECTION_RETENTION_DAYS",
            str(days),
        )
        config = load_config()
        assert config.storage.detection_retention_days == days

    @_PBT_SETTINGS
    @given(days=st.integers(min_value=-100, max_value=0))
    def test_detection_retention_days_below_minimum_rejected(
        self, days, monkeypatch
    ):
        """Valores abaixo de 1 devem levantar ValueError."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_STORAGE_DETECTION_RETENTION_DAYS",
            str(days),
        )
        with pytest.raises(
            ValueError, match="detection_retention_days"
        ):
            load_config()

    @_PBT_SETTINGS
    @given(days=st.integers(min_value=366, max_value=500))
    def test_detection_retention_days_above_maximum_rejected(
        self, days, monkeypatch
    ):
        """Valores acima de 365 devem levantar ValueError."""
        monkeypatch.setenv(
            "BRAND_WATCHDOG_STORAGE_DETECTION_RETENTION_DAYS",
            str(days),
        )
        with pytest.raises(
            ValueError, match="detection_retention_days"
        ):
            load_config()
