"""Property tests para persistência de resultado por site.

# Feature: architecture-evolution, Property 4: Completude da Persistência de Resultado por Site

**Validates: Requirements 3.4, 3.5**

Property 4: Para qualquer resultado de processamento de um site
(sucesso ou falha), o registro persistido no banco SHALL conter:
site_id, cycle_id, status ("success" ou "failure"), timestamp de
conclusão, e adicionalmente detections_count (se sucesso) ou
failure_reason (se falha).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from brand_watchdog.models.entities import (
    Base,
    MonitoringCycleModel,
    SiteCycleResultModel,
    TargetSiteModel,
)


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# --- Strategies ---

_uuid_strategy = st.uuids().map(str)

_status_strategy = st.sampled_from(["success", "failure"])

_detections_count_strategy = st.integers(min_value=0, max_value=10000)

_failure_reason_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

_completed_at_strategy = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(timezone.utc),
)


# --- Fixtures ---


@pytest.fixture
def db_session():
    """Cria banco SQLite em memória com schema para SiteCycleResult."""
    engine = create_engine("sqlite:///:memory:")

    # Habilitar foreign keys no SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Criar tabelas necessárias (target_sites, monitoring_cycles,
    # site_cycle_results)
    Base.metadata.create_all(bind=engine)

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    yield session

    session.close()
    engine.dispose()


def _create_target_site(session: Session) -> str:
    """Cria um TargetSiteModel e retorna o id."""
    site_id = str(uuid.uuid4())
    site = TargetSiteModel(
        id=site_id,
        url=f"https://site-{site_id[:8]}.example.com",
        normalized_url=f"https://site-{site_id[:8]}.example.com",
        brand="sky_plus",
        active=True,
    )
    session.add(site)
    session.commit()
    return site_id


def _create_monitoring_cycle(session: Session) -> str:
    """Cria um MonitoringCycleModel e retorna o id."""
    cycle_id = str(uuid.uuid4())
    cycle = MonitoringCycleModel(
        id=cycle_id,
        started_at=datetime.now(timezone.utc),
        status=MonitoringCycleModel.STATUS_DISPATCHED,
    )
    session.add(cycle)
    session.commit()
    return cycle_id


# --- Property Tests ---


class TestSiteResultPersistence:
    """Property 4: Completude da Persistência de Resultado por Site.

    Para qualquer resultado de processamento de um site (sucesso ou
    falha), o registro persistido no banco SHALL conter: site_id,
    cycle_id, status ("success" ou "failure"), timestamp de conclusão,
    e adicionalmente detections_count (se sucesso) ou failure_reason
    (se falha).

    **Validates: Requirements 3.4, 3.5**
    """

    @_PBT_SETTINGS
    @given(
        status=_status_strategy,
        detections_count=_detections_count_strategy,
        failure_reason=_failure_reason_strategy,
        completed_at=_completed_at_strategy,
    )
    def test_required_fields_are_present_and_non_null(
        self,
        status: str,
        detections_count: int,
        failure_reason: str,
        completed_at: datetime,
        db_session: Session,
    ) -> None:
        """Todos os campos obrigatórios (site_id, cycle_id, status,
        completed_at) devem estar presentes e não-nulos no registro
        persistido."""
        site_id = _create_target_site(db_session)
        cycle_id = _create_monitoring_cycle(db_session)

        result_id = str(uuid.uuid4())
        record = SiteCycleResultModel(
            id=result_id,
            site_id=site_id,
            cycle_id=cycle_id,
            status=status,
            detections_count=(
                detections_count if status == "success" else 0
            ),
            failure_reason=(
                failure_reason if status == "failure" else None
            ),
            completed_at=completed_at,
        )
        db_session.add(record)
        db_session.commit()

        # Recuperar do banco
        persisted = (
            db_session.query(SiteCycleResultModel)
            .filter_by(id=result_id)
            .one()
        )

        # Campos obrigatórios presentes e não-nulos
        assert persisted.site_id is not None
        assert persisted.cycle_id is not None
        assert persisted.status is not None
        assert persisted.completed_at is not None

        # Campos correspondem ao inserido
        assert persisted.site_id == site_id
        assert persisted.cycle_id == cycle_id
        assert persisted.status == status

        # Cleanup para próxima iteração
        db_session.query(SiteCycleResultModel).delete()
        db_session.query(MonitoringCycleModel).delete()
        db_session.query(TargetSiteModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        status=_status_strategy,
    )
    def test_status_is_success_or_failure(
        self,
        status: str,
        db_session: Session,
    ) -> None:
        """O campo status SHALL ser 'success' ou 'failure'."""
        site_id = _create_target_site(db_session)
        cycle_id = _create_monitoring_cycle(db_session)

        result_id = str(uuid.uuid4())
        record = SiteCycleResultModel(
            id=result_id,
            site_id=site_id,
            cycle_id=cycle_id,
            status=status,
            detections_count=0,
            failure_reason=None if status == "success" else "error",
            completed_at=datetime.now(timezone.utc),
        )
        db_session.add(record)
        db_session.commit()

        persisted = (
            db_session.query(SiteCycleResultModel)
            .filter_by(id=result_id)
            .one()
        )

        assert persisted.status in ("success", "failure")

        # Cleanup
        db_session.query(SiteCycleResultModel).delete()
        db_session.query(MonitoringCycleModel).delete()
        db_session.query(TargetSiteModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        detections_count=_detections_count_strategy,
        completed_at=_completed_at_strategy,
    )
    def test_success_has_detections_count_gte_zero(
        self,
        detections_count: int,
        completed_at: datetime,
        db_session: Session,
    ) -> None:
        """Se status='success', detections_count SHALL ser >= 0."""
        site_id = _create_target_site(db_session)
        cycle_id = _create_monitoring_cycle(db_session)

        result_id = str(uuid.uuid4())
        record = SiteCycleResultModel(
            id=result_id,
            site_id=site_id,
            cycle_id=cycle_id,
            status="success",
            detections_count=detections_count,
            failure_reason=None,
            completed_at=completed_at,
        )
        db_session.add(record)
        db_session.commit()

        persisted = (
            db_session.query(SiteCycleResultModel)
            .filter_by(id=result_id)
            .one()
        )

        assert persisted.detections_count >= 0

        # Cleanup
        db_session.query(SiteCycleResultModel).delete()
        db_session.query(MonitoringCycleModel).delete()
        db_session.query(TargetSiteModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        failure_reason=_failure_reason_strategy,
        completed_at=_completed_at_strategy,
    )
    def test_failure_has_non_empty_failure_reason(
        self,
        failure_reason: str,
        completed_at: datetime,
        db_session: Session,
    ) -> None:
        """Se status='failure', failure_reason SHALL ser não-nulo
        e não-vazio."""
        site_id = _create_target_site(db_session)
        cycle_id = _create_monitoring_cycle(db_session)

        result_id = str(uuid.uuid4())
        record = SiteCycleResultModel(
            id=result_id,
            site_id=site_id,
            cycle_id=cycle_id,
            status="failure",
            detections_count=0,
            failure_reason=failure_reason,
            completed_at=completed_at,
        )
        db_session.add(record)
        db_session.commit()

        persisted = (
            db_session.query(SiteCycleResultModel)
            .filter_by(id=result_id)
            .one()
        )

        assert persisted.failure_reason is not None
        assert len(persisted.failure_reason.strip()) > 0

        # Cleanup
        db_session.query(SiteCycleResultModel).delete()
        db_session.query(MonitoringCycleModel).delete()
        db_session.query(TargetSiteModel).delete()
        db_session.commit()

    @_PBT_SETTINGS
    @given(
        completed_at=_completed_at_strategy,
    )
    def test_unique_site_cycle_constraint(
        self,
        completed_at: datetime,
        db_session: Session,
    ) -> None:
        """A constraint UNIQUE(site_id, cycle_id) SHALL impedir
        registros duplicados para o mesmo par."""
        site_id = _create_target_site(db_session)
        cycle_id = _create_monitoring_cycle(db_session)

        # Primeiro registro: deve funcionar
        record_1 = SiteCycleResultModel(
            id=str(uuid.uuid4()),
            site_id=site_id,
            cycle_id=cycle_id,
            status="success",
            detections_count=3,
            failure_reason=None,
            completed_at=completed_at,
        )
        db_session.add(record_1)
        db_session.commit()

        # Segundo registro com mesmo (site_id, cycle_id): deve falhar
        record_2 = SiteCycleResultModel(
            id=str(uuid.uuid4()),
            site_id=site_id,
            cycle_id=cycle_id,
            status="failure",
            detections_count=0,
            failure_reason="duplicado",
            completed_at=completed_at,
        )
        db_session.add(record_2)

        with pytest.raises(IntegrityError):
            db_session.commit()

        db_session.rollback()

        # Cleanup
        db_session.query(SiteCycleResultModel).delete()
        db_session.query(MonitoringCycleModel).delete()
        db_session.query(TargetSiteModel).delete()
        db_session.commit()
