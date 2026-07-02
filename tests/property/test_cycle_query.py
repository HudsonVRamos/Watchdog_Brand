"""Property tests para filtragem de ciclos por versão de regras.

# Feature: architecture-evolution, Property 12: Filtragem de Ciclos por Versão de Regras

**Validates: Requirements 7.6**

Property 12: Para qualquer conjunto de ciclos com diferentes
rule_set_version, uma consulta filtrando por uma versão específica
SHALL retornar apenas ciclos cujo rule_set_version é exatamente
igual ao filtro aplicado.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from brand_watchdog.models.entities import (
    Base,
    MonitoringCycleModel,
)


_PBT_SETTINGS = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# --- Strategies ---

# Gera rule_set_version no formato "v{timestamp_unix}_{hash_8_chars}"
_timestamp_strategy = st.integers(min_value=1_000_000_000, max_value=2_000_000_000)
_hash_8_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=8,
    max_size=8,
)


@st.composite
def _rule_set_version_strategy(draw):
    """Gera uma rule_set_version válida no formato v{ts}_{hash8}."""
    ts = draw(_timestamp_strategy)
    h = draw(_hash_8_strategy)
    return f"v{ts}_{h}"


@st.composite
def _distinct_versions_strategy(draw):
    """Gera lista de 2-10 versões distintas de rule_set_version."""
    num_versions = draw(st.integers(min_value=2, max_value=10))
    versions = draw(
        st.lists(
            _rule_set_version_strategy(),
            min_size=num_versions,
            max_size=num_versions,
            unique=True,
        )
    )
    return versions


# --- Fixtures ---


@pytest.fixture
def db_engine():
    """Cria engine SQLite em memória com schema completo."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)

    yield engine

    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Cria session para cada teste."""
    session_factory = sessionmaker(bind=db_engine)
    session = session_factory()

    yield session

    session.close()


def _create_cycle_with_version(
    session: Session, rule_set_version: str
) -> str:
    """Cria um MonitoringCycleModel com a versão de regras especificada."""
    cycle_id = str(uuid.uuid4())
    cycle = MonitoringCycleModel(
        id=cycle_id,
        started_at=datetime.now(timezone.utc),
        status=MonitoringCycleModel.STATUS_COMPLETED,
        rule_set_version=rule_set_version,
    )
    session.add(cycle)
    return cycle_id


# --- Property Tests ---


class TestCycleVersionFiltering:
    """Property 12: Filtragem de Ciclos por Versão de Regras.

    Para qualquer conjunto de ciclos com diferentes rule_set_version,
    uma consulta filtrando por uma versão específica SHALL retornar
    apenas ciclos cujo rule_set_version é exatamente igual ao filtro
    aplicado.

    **Validates: Requirements 7.6**
    """

    @_PBT_SETTINGS
    @given(data=st.data())
    def test_filter_returns_only_cycles_with_exact_version(
        self,
        data: st.DataObject,
        db_session: Session,
    ) -> None:
        """Filtragem por rule_set_version retorna APENAS ciclos com
        a versão exata especificada no filtro."""
        # Limpar tabela antes de cada iteração
        db_session.query(MonitoringCycleModel).delete()
        db_session.commit()

        # Gerar 2-10 versões distintas
        versions = data.draw(_distinct_versions_strategy())

        # Criar ciclos com diferentes versões (1-3 ciclos por versão)
        created_cycles: dict[str, list[str]] = {}
        for version in versions:
            num_cycles = data.draw(
                st.integers(min_value=1, max_value=3)
            )
            created_cycles[version] = []
            for _ in range(num_cycles):
                cycle_id = _create_cycle_with_version(
                    db_session, version
                )
                created_cycles[version].append(cycle_id)

        db_session.commit()

        # Escolher uma versão aleatória como filtro
        filter_version = data.draw(st.sampled_from(versions))

        # Executar query filtrando por rule_set_version
        results = (
            db_session.query(MonitoringCycleModel)
            .filter(
                MonitoringCycleModel.rule_set_version == filter_version
            )
            .all()
        )

        # Verificar que TODOS os ciclos retornados têm a versão filtrada
        for cycle in results:
            assert cycle.rule_set_version == filter_version, (
                f"Ciclo {cycle.id} retornado com versão "
                f"'{cycle.rule_set_version}' mas filtro era "
                f"'{filter_version}'"
            )

        # Verificar que NENHUM ciclo com versão diferente foi retornado
        returned_ids = {c.id for c in results}
        for version, cycle_ids in created_cycles.items():
            if version != filter_version:
                for cid in cycle_ids:
                    assert cid not in returned_ids, (
                        f"Ciclo {cid} com versão '{version}' não "
                        f"deveria ter sido retornado pelo filtro "
                        f"'{filter_version}'"
                    )

        # Verificar completude: todos ciclos esperados foram retornados
        expected_ids = set(created_cycles[filter_version])
        assert returned_ids == expected_ids, (
            f"Ciclos retornados ({returned_ids}) diferem dos "
            f"esperados ({expected_ids}) para versão "
            f"'{filter_version}'"
        )

    @_PBT_SETTINGS
    @given(data=st.data())
    def test_filter_with_nonexistent_version_returns_empty(
        self,
        data: st.DataObject,
        db_session: Session,
    ) -> None:
        """Filtragem por versão inexistente retorna lista vazia."""
        # Limpar tabela antes de cada iteração
        db_session.query(MonitoringCycleModel).delete()
        db_session.commit()

        # Gerar versões e criar ciclos
        versions = data.draw(_distinct_versions_strategy())
        for version in versions:
            _create_cycle_with_version(db_session, version)
        db_session.commit()

        # Gerar uma versão que NÃO está no conjunto
        nonexistent_version = data.draw(_rule_set_version_strategy())
        assume(nonexistent_version not in versions)

        # Filtrar por versão inexistente
        results = (
            db_session.query(MonitoringCycleModel)
            .filter(
                MonitoringCycleModel.rule_set_version
                == nonexistent_version
            )
            .all()
        )

        assert len(results) == 0, (
            f"Filtro por versão inexistente '{nonexistent_version}' "
            f"retornou {len(results)} resultados (esperado: 0)"
        )

    @_PBT_SETTINGS
    @given(data=st.data())
    def test_filter_count_matches_inserted_cycles(
        self,
        data: st.DataObject,
        db_session: Session,
    ) -> None:
        """O número de ciclos retornados pelo filtro é igual ao
        número de ciclos inseridos com aquela versão."""
        # Limpar tabela antes de cada iteração
        db_session.query(MonitoringCycleModel).delete()
        db_session.commit()

        # Gerar 2-10 versões distintas
        versions = data.draw(_distinct_versions_strategy())

        # Criar ciclos com contagens conhecidas
        version_counts: dict[str, int] = {}
        for version in versions:
            count = data.draw(st.integers(min_value=1, max_value=5))
            version_counts[version] = count
            for _ in range(count):
                _create_cycle_with_version(db_session, version)

        db_session.commit()

        # Verificar cada versão
        filter_version = data.draw(st.sampled_from(versions))
        results = (
            db_session.query(MonitoringCycleModel)
            .filter(
                MonitoringCycleModel.rule_set_version == filter_version
            )
            .all()
        )

        assert len(results) == version_counts[filter_version], (
            f"Para versão '{filter_version}' esperava "
            f"{version_counts[filter_version]} ciclos, "
            f"mas obteve {len(results)}"
        )
