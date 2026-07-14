"""Property tests para notificação por email.

# Feature: architecture-evolution, Property 8: Completude do Conteúdo de Notificação por Email
# Feature: architecture-evolution, Property 9: Idempotência de Notificação

**Validates: Requirements 6.2, 6.5, 6.6**

Property 8: Para qualquer ComplianceCompletedEvent recebido pelo
ComplianceEmailNotifier, o email enviado SHALL conter: target_url,
overall_status, e exatamente 6 regras com rule_id, status
(PASS/FAIL/NOT_APPLICABLE), confidence (0-100%) e descrição textual.

Property 9: Para qualquer par (cycle_id, target_url), se o
ComplianceEmailNotifier processar o mesmo evento duas ou mais vezes,
apenas o primeiro processamento SHALL resultar em envio de email.
Processamentos subsequentes com o mesmo par SHALL ser descartados
sem reenvio.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from brand_watchdog.alerts.compliance_email_notifier import (
    ComplianceEmailNotifier,
)
from brand_watchdog.config import AlertConfig
from brand_watchdog.events.models import ComplianceCompletedEvent
from brand_watchdog.models.dataclasses import (
    COMPLIANCE_RULES,
    ComplianceReport,
    ComplianceRuleResult,
)
from brand_watchdog.models.entities import (
    NotificationDedupModel,
)


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Statuses válidos para regras de compliance
_VALID_RULE_STATUSES = ("PASS", "FAIL", "NOT_APPLICABLE")


# --- Estratégias de geração de dados ---

_uuid_strategy = st.uuids().map(str)

_url_strategy = st.from_regex(
    r"https://[a-z]{3,15}\.[a-z]{2,5}(/[a-z0-9_\-]{1,30}){0,4}",
    fullmatch=True,
).filter(lambda u: len(u) <= 2048)

_brand_strategy = st.sampled_from(["sky_plus", "dgo"])

_overall_status_strategy = st.sampled_from(
    ["compliant", "non_compliant"]
)

_repeat_count_strategy = st.integers(min_value=2, max_value=5)


@st.composite
def compliance_completed_event_strategy(
    draw: st.DrawFn,
) -> ComplianceCompletedEvent:
    """Gera um ComplianceCompletedEvent válido com exatamente 6 regras.

    Cada regra em COMPLIANCE_RULES terá: rule_id, status, confidence
    e description.
    """
    site_id = draw(_uuid_strategy)
    cycle_id = draw(_uuid_strategy)
    target_url = draw(_url_strategy)
    brand = draw(_brand_strategy)
    overall_status = draw(_overall_status_strategy)

    rule_results = []
    for rule_id in COMPLIANCE_RULES:
        status = draw(st.sampled_from(list(_VALID_RULE_STATUSES)))
        confidence = draw(st.integers(min_value=0, max_value=100))
        description = draw(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("L", "N", "P", "Z"),
                ),
                min_size=1,
                max_size=200,
            )
        )
        rule_results.append({
            "rule_id": rule_id,
            "status": status,
            "confidence": confidence,
            "description": description,
        })

    screenshot_s3_key = draw(
        st.from_regex(
            r"screenshots/[a-f0-9\-]{36}/[a-f0-9\-]{36}\.png",
            fullmatch=True,
        )
    )
    analyzed_at = draw(
        st.datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 12, 31),
            timezones=st.just(timezone.utc),
        ).map(lambda dt: dt.isoformat())
    )

    return ComplianceCompletedEvent(
        site_id=site_id,
        cycle_id=cycle_id,
        target_url=target_url,
        brand=brand,
        overall_status=overall_status,
        rule_results=rule_results,
        screenshot_s3_key=screenshot_s3_key,
        analyzed_at=analyzed_at,
    )


def _create_notifier() -> ComplianceEmailNotifier:
    """Cria instância mínima do ComplianceEmailNotifier para testes."""
    config = AlertConfig(
        ses_sender="test@example.com",
        recipients=["recipient@example.com"],
    )
    return ComplianceEmailNotifier(config=config)


def _event_to_report(
    event_data: ComplianceCompletedEvent,
) -> ComplianceReport:
    """Converte ComplianceCompletedEvent em ComplianceReport.

    Simula o fluxo do EventNotificationHandler que converte o
    evento recebido do EventBridge em um ComplianceReport para
    formatação de email.
    """
    rule_results = [
        ComplianceRuleResult(
            rule_id=r["rule_id"],
            status=r["status"],
            confidence=r["confidence"],
            description=r["description"],
        )
        for r in event_data.rule_results
    ]
    return ComplianceReport(
        target_url=event_data.target_url,
        analyzed_at=datetime.fromisoformat(event_data.analyzed_at),
        overall_status=event_data.overall_status,
        rule_results=rule_results,
        screenshot_ref_id=event_data.screenshot_s3_key,
        cycle_id=event_data.cycle_id,
    )


# --- Property 8: Completude do Conteúdo de Notificação ---


class TestEmailNotificationContent:
    """Property 8: Completude do Conteúdo de Notificação por Email.

    Para qualquer ComplianceCompletedEvent recebido pelo
    ComplianceEmailNotifier, o email enviado SHALL conter:
    target_url, overall_status, e exatamente 6 regras com rule_id,
    status (PASS/FAIL/NOT_APPLICABLE), confidence (0-100%) e
    descrição textual.

    **Validates: Requirements 6.2, 6.5**
    """

    @_PBT_SETTINGS
    @given(event_data=compliance_completed_event_strategy())
    def test_email_notification_content_completeness(
        self, event_data: ComplianceCompletedEvent
    ) -> None:
        """Para qualquer ComplianceCompletedEvent, o email SHALL conter:
        - target_url
        - overall_status
        - Exatamente 6 regras, cada uma com:
          - rule_id
          - status (PASS/FAIL/NOT_APPLICABLE)
          - confidence (0-100%)
          - descrição textual
        """
        notifier = _create_notifier()
        report = _event_to_report(event_data)
        _subject, body = notifier._format_compliance_email(report)

        # 1. target_url presente
        assert event_data.target_url in body, (
            f"target_url '{event_data.target_url}' ausente "
            f"no email body"
        )

        # 2. overall_status presente (em uppercase)
        expected_status = event_data.overall_status.upper()
        assert expected_status in body, (
            f"overall_status '{expected_status}' ausente "
            f"no email body"
        )

        # 3. Exatamente 6 regras com todos os campos
        assert len(event_data.rule_results) == 6, (
            f"Esperado 6 regras, encontrado "
            f"{len(event_data.rule_results)}"
        )

        for rule in event_data.rule_results:
            # rule_id
            assert rule["rule_id"] in body, (
                f"rule_id '{rule['rule_id']}' ausente no "
                f"email body"
            )
            # status (PASS/FAIL/NOT_APPLICABLE)
            assert rule["status"] in body, (
                f"status '{rule['status']}' da regra "
                f"'{rule['rule_id']}' ausente no email body"
            )
            # confidence como XX%
            confidence_str = f"{rule['confidence']}%"
            assert confidence_str in body, (
                f"confidence '{confidence_str}' da regra "
                f"'{rule['rule_id']}' ausente no email body"
            )
            # descrição textual presente
            desc = rule["description"]
            assert desc in body, (
                f"descrição da regra '{rule['rule_id']}' "
                f"ausente no email body"
            )



# --- Helper: simula handler de notificação com deduplicação ---


class NotificationHandlerSimulator:
    """Simula o comportamento do EventNotificationHandler.

    Implementa a lógica de deduplicação via tabela notification_dedup:
    - Antes de enviar, verifica se (cycle_id, target_url) já existe
    - Se não existe, insere o registro e envia o email
    - Se já existe, descarta sem reenviar

    Usa banco SQLite em memória para testar a constraint UNIQUE.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self.emails_sent: list[tuple[str, str]] = []

    def process_event(
        self,
        cycle_id: str,
        target_url: str,
    ) -> bool:
        """Processa um evento de compliance para notificação.

        Returns:
            True se email foi enviado (primeira vez).
            False se descartado (duplicata).
        """
        # Verificar se já foi processado
        existing = (
            self._session.query(NotificationDedupModel)
            .filter_by(cycle_id=cycle_id, target_url=target_url)
            .first()
        )

        if existing is not None:
            # Descarta: já processado
            return False

        # Registrar deduplicação
        dedup_record = NotificationDedupModel(
            id=str(uuid.uuid4()),
            cycle_id=cycle_id,
            target_url=target_url,
            processed_at=datetime.now(timezone.utc),
        )
        self._session.add(dedup_record)
        try:
            self._session.commit()
        except IntegrityError:
            # Race condition: outro processo inseriu primeiro
            self._session.rollback()
            return False

        # Envia email (simulado)
        self.emails_sent.append((cycle_id, target_url))
        return True


# --- Fixtures ---


@pytest.fixture
def db_session():
    """Cria banco SQLite em memória com schema notification_dedup."""
    engine = create_engine("sqlite:///:memory:")

    # Habilitar foreign keys no SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Criar apenas a tabela notification_dedup
    NotificationDedupModel.__table__.create(bind=engine)

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    yield session

    session.close()
    engine.dispose()


# --- Property Tests ---


class TestNotificationIdempotency:
    """Property 9: Idempotência de Notificação.

    **Validates: Requirements 6.6**
    """

    @_PBT_SETTINGS
    @given(
        cycle_id=_uuid_strategy,
        target_url=_url_strategy,
        repeat_count=_repeat_count_strategy,
    )
    def test_same_event_only_sends_email_once(
        self,
        cycle_id: str,
        target_url: str,
        repeat_count: int,
        db_session: Session,
    ) -> None:
        """Para qualquer par (cycle_id, target_url) processado N vezes,
        apenas o primeiro processamento SHALL enviar email.

        Processamentos subsequentes (2..N) devem ser descartados.
        """
        handler = NotificationHandlerSimulator(db_session)

        results = []
        for _ in range(repeat_count):
            result = handler.process_event(cycle_id, target_url)
            results.append(result)

        # Apenas o primeiro processamento retorna True (email enviado)
        assert results[0] is True, (
            "Primeiro processamento deveria enviar email"
        )

        # Todos os subsequentes retornam False (descartados)
        for i, result in enumerate(results[1:], start=2):
            assert result is False, (
                f"Processamento #{i} deveria ser descartado, "
                f"mas retornou True"
            )

        # Exatamente 1 email enviado
        assert len(handler.emails_sent) == 1, (
            f"Deveria ter enviado exatamente 1 email, "
            f"mas enviou {len(handler.emails_sent)}"
        )

        # O email enviado corresponde ao par correto
        assert handler.emails_sent[0] == (cycle_id, target_url)

        # Cleanup para próxima iteração do Hypothesis
        db_session.query(NotificationDedupModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        cycle_id=_uuid_strategy,
        url_1=_url_strategy,
        url_2=_url_strategy,
    )
    def test_different_urls_same_cycle_are_independent(
        self,
        cycle_id: str,
        url_1: str,
        url_2: str,
        db_session: Session,
    ) -> None:
        """Para o mesmo cycle_id, URLs diferentes devem resultar
        em emails independentes (cada par é único)."""
        assume(url_1 != url_2)

        handler = NotificationHandlerSimulator(db_session)

        # Processar par 1
        result_1 = handler.process_event(cycle_id, url_1)
        # Processar par 2 (URL diferente)
        result_2 = handler.process_event(cycle_id, url_2)

        # Ambos devem enviar email (pares diferentes)
        assert result_1 is True, (
            f"Primeiro par ({cycle_id}, {url_1}) deveria enviar"
        )
        assert result_2 is True, (
            f"Segundo par ({cycle_id}, {url_2}) deveria enviar"
        )

        # Total de 2 emails enviados
        assert len(handler.emails_sent) == 2

        # Cleanup
        db_session.query(NotificationDedupModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        cycle_id_1=_uuid_strategy,
        cycle_id_2=_uuid_strategy,
        target_url=_url_strategy,
    )
    def test_different_cycles_same_url_are_independent(
        self,
        cycle_id_1: str,
        cycle_id_2: str,
        target_url: str,
        db_session: Session,
    ) -> None:
        """Para a mesma URL, cycles diferentes devem resultar
        em emails independentes (cada par é único)."""
        assume(cycle_id_1 != cycle_id_2)

        handler = NotificationHandlerSimulator(db_session)

        # Processar par 1
        result_1 = handler.process_event(cycle_id_1, target_url)
        # Processar par 2 (cycle diferente)
        result_2 = handler.process_event(cycle_id_2, target_url)

        # Ambos devem enviar email (pares diferentes)
        assert result_1 is True, (
            f"Primeiro par ({cycle_id_1}, {target_url}) "
            f"deveria enviar"
        )
        assert result_2 is True, (
            f"Segundo par ({cycle_id_2}, {target_url}) "
            f"deveria enviar"
        )

        # Total de 2 emails enviados
        assert len(handler.emails_sent) == 2

        # Cleanup
        db_session.query(NotificationDedupModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        cycle_id=_uuid_strategy,
        target_url=_url_strategy,
        repeat_count=_repeat_count_strategy,
    )
    def test_dedup_record_persisted_after_first_processing(
        self,
        cycle_id: str,
        target_url: str,
        repeat_count: int,
        db_session: Session,
    ) -> None:
        """Após processar um evento, exatamente 1 registro de dedup
        é persistido na tabela notification_dedup com o par correto."""
        handler = NotificationHandlerSimulator(db_session)

        # Processa múltiplas vezes
        for _ in range(repeat_count):
            handler.process_event(cycle_id, target_url)

        # Verificar que há exatamente 1 registro no banco
        records = (
            db_session.query(NotificationDedupModel)
            .filter_by(cycle_id=cycle_id, target_url=target_url)
            .all()
        )

        assert len(records) == 1, (
            f"Deveria ter exatamente 1 registro de dedup, "
            f"mas encontrou {len(records)}"
        )

        # Verificar campos do registro
        record = records[0]
        assert record.cycle_id == cycle_id
        assert record.target_url == target_url
        assert record.processed_at is not None
        assert record.id is not None

        # Cleanup
        db_session.query(NotificationDedupModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        cycle_id=_uuid_strategy,
        target_url=_url_strategy,
    )
    def test_unique_constraint_prevents_duplicate_insert(
        self,
        cycle_id: str,
        target_url: str,
        db_session: Session,
    ) -> None:
        """A constraint UNIQUE(cycle_id, target_url) impede inserção
        de registros duplicados na tabela notification_dedup."""
        # Primeiro insert: deve ter sucesso
        record_1 = NotificationDedupModel(
            id=str(uuid.uuid4()),
            cycle_id=cycle_id,
            target_url=target_url,
            processed_at=datetime.now(timezone.utc),
        )
        db_session.add(record_1)
        db_session.commit()

        # Segundo insert com mesmo par: deve falhar com IntegrityError
        record_2 = NotificationDedupModel(
            id=str(uuid.uuid4()),
            cycle_id=cycle_id,
            target_url=target_url,
            processed_at=datetime.now(timezone.utc),
        )
        db_session.add(record_2)

        with pytest.raises(IntegrityError):
            db_session.commit()

        db_session.rollback()

        # Cleanup
        db_session.query(NotificationDedupModel).delete()
        db_session.commit()
