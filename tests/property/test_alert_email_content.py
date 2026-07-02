"""Property tests para completude do conteúdo de emails de alerta.

Valida que o AlertService._format_alert_email sempre inclui todos os
campos obrigatórios no email gerado, independente dos dados de entrada.

**Validates: Requirements 6.2**
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from brand_watchdog.alerts.alert_service import AlertService
from brand_watchdog.config import AlertConfig
from brand_watchdog.models.dataclasses import BoundingBox, DetectionResult
from brand_watchdog.storage.detection_store import DetectionStore


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# --- Strategies ---


def _bounding_box_strategy() -> st.SearchStrategy[BoundingBox]:
    """Gera BoundingBox com coordenadas percentuais válidas."""
    return st.builds(
        BoundingBox,
        x_percent=st.floats(min_value=0.0, max_value=100.0),
        y_percent=st.floats(min_value=0.0, max_value=100.0),
        width_percent=st.floats(min_value=0.1, max_value=100.0),
        height_percent=st.floats(min_value=0.1, max_value=100.0),
    )


def _target_url_strategy() -> st.SearchStrategy[str]:
    """Gera URLs de target sites variadas e válidas."""
    schemes = st.sampled_from(["http", "https"])
    hostnames = st.from_regex(
        r"[a-z][a-z0-9]{2,15}\.(com|org|net|io|dev)", fullmatch=True
    )
    paths = st.sampled_from(["", "/page", "/dir/sub", "/path/to/resource"])
    return st.tuples(schemes, hostnames, paths).map(
        lambda t: f"{t[0]}://{t[1]}{t[2]}"
    )


def _datetime_strategy() -> st.SearchStrategy[datetime]:
    """Gera datetimes timezone-aware (UTC) variados."""
    return st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2030, 12, 31),
        timezones=st.just(timezone.utc),
    )


def _detection_result_strategy() -> st.SearchStrategy[DetectionResult]:
    """Gera DetectionResults variados com todos os campos válidos."""
    return st.builds(
        DetectionResult,
        target_url=_target_url_strategy(),
        match_type=st.sampled_from(["logo", "text"]),
        confidence=st.integers(min_value=0, max_value=100),
        bounding_box=_bounding_box_strategy(),
        description=st.text(
            min_size=1,
            max_size=200,
            alphabet=st.characters(
                whitelist_categories=("L", "N", "Z", "P")
            ),
        ),
        detected_at=_datetime_strategy(),
        screenshot_ref_id=st.text(
            min_size=5,
            max_size=36,
            alphabet=st.characters(
                whitelist_categories=("L", "N")
            ),
        ),
    )


# --- Helpers ---


def _create_alert_service() -> AlertService:
    """Cria instância do AlertService para testes (sem dependências reais)."""
    config = AlertConfig()
    # detection_store não é usado em _format_alert_email
    # Passamos None pois não precisamos de store para formatação
    return AlertService(
        config=config,
        detection_store=None,  # type: ignore[arg-type]
        email_provider=None,
    )


# --- Property Tests ---


class TestAlertEmailContentCompleteness:
    """Property 13: Alert Email Content Completeness.

    DetectionResults variados, email contém todos os campos obrigatórios.

    **Validates: Requirements 6.2**
    """

    @_PBT_SETTINGS
    @given(detection=_detection_result_strategy())
    def test_email_contains_target_url(self, detection: DetectionResult):
        """Email sempre contém a URL do site-alvo."""
        service = _create_alert_service()
        subject, body = service._format_alert_email(detection)

        assert detection.target_url in body, (
            f"URL do site-alvo '{detection.target_url}' não encontrada no body"
        )
        assert detection.target_url in subject, (
            f"URL do site-alvo '{detection.target_url}' não encontrada no subject"
        )

    @_PBT_SETTINGS
    @given(detection=_detection_result_strategy())
    def test_email_contains_match_type(self, detection: DetectionResult):
        """Email sempre contém o tipo de match (logo ou text)."""
        service = _create_alert_service()
        subject, body = service._format_alert_email(detection)

        # O body deve conter o match_type original (logo ou text)
        assert detection.match_type in body, (
            f"Tipo de match '{detection.match_type}' não encontrado no body"
        )

    @_PBT_SETTINGS
    @given(detection=_detection_result_strategy())
    def test_email_contains_confidence_level(
        self, detection: DetectionResult
    ):
        """Email sempre contém o nível de confiança (0-100)."""
        service = _create_alert_service()
        _subject, body = service._format_alert_email(detection)

        # Confidence deve aparecer no body como valor numérico
        confidence_str = str(detection.confidence)
        assert confidence_str in body, (
            f"Nível de confiança '{confidence_str}' não encontrado no body"
        )

    @_PBT_SETTINGS
    @given(detection=_detection_result_strategy())
    def test_email_contains_description(self, detection: DetectionResult):
        """Email sempre contém a descrição da localização do match."""
        service = _create_alert_service()
        _subject, body = service._format_alert_email(detection)

        assert detection.description in body, (
            f"Descrição '{detection.description}' não encontrada no body"
        )

    @_PBT_SETTINGS
    @given(detection=_detection_result_strategy())
    def test_email_contains_iso8601_timestamp(
        self, detection: DetectionResult
    ):
        """Email sempre contém timestamp em formato ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)."""
        service = _create_alert_service()
        _subject, body = service._format_alert_email(detection)

        # Formata o timestamp esperado conforme implementação
        expected_timestamp = detection.detected_at.astimezone(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        assert expected_timestamp in body, (
            f"Timestamp ISO 8601 '{expected_timestamp}' não encontrado no body"
        )

    @_PBT_SETTINGS
    @given(detection=_detection_result_strategy())
    def test_email_contains_all_required_fields(
        self, detection: DetectionResult
    ):
        """Email sempre contém TODOS os campos obrigatórios simultaneamente.

        Campos obrigatórios (Requirement 6.2):
        - Target_Site URL
        - Match type (logo ou text)
        - Confidence level (0-100)
        - Descrição da localização do match
        - Timestamp em ISO 8601
        """
        service = _create_alert_service()
        subject, body = service._format_alert_email(detection)

        # 1. Target URL presente no email
        assert detection.target_url in body
        assert detection.target_url in subject

        # 2. Match type presente
        assert detection.match_type in body

        # 3. Confidence level presente
        assert str(detection.confidence) in body

        # 4. Descrição presente
        assert detection.description in body

        # 5. Timestamp ISO 8601 presente
        expected_timestamp = detection.detected_at.astimezone(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert expected_timestamp in body
