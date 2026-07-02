"""Property tests para Query Filtering Correctness.

**Validates: Requirements 7.5**

Property 18: Query Filtering Correctness — conjuntos de detecções + filtros,
resultados respeitam todos os filtros aplicados e paginação.

Garante que:
- Todos os resultados retornados respeitam TODOS os filtros aplicados
- Nenhum resultado que deveria casar com os filtros é omitido (completude)
- Resultados estão em ordem cronológica reversa
- Paginação funciona corretamente (page_size respeitado, has_next correto)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st
from sqlalchemy import delete

from brand_watchdog.config import StorageConfig
from brand_watchdog.models.database import (
    close_db,
    get_session,
    init_db,
    setup_database,
)
from brand_watchdog.models.dataclasses import (
    BoundingBox,
    DetectionResult,
)
from brand_watchdog.models.entities import (
    DetectionResultModel,
    MonitoringCycleModel,
    ScreenshotModel,
    TargetSiteModel,
)
from brand_watchdog.storage.detection_store import DetectionStore


# Estratégias de geração de dados

_MATCH_TYPES = ["logo", "text"]

_TARGET_URLS = [
    "https://site-a.com",
    "https://site-b.com",
    "https://site-c.com",
    "https://site-d.com",
]


def _st_match_type() -> st.SearchStrategy[str]:
    """Estratégia para gerar match_type válido."""
    return st.sampled_from(_MATCH_TYPES)


def _st_target_url() -> st.SearchStrategy[str]:
    """Estratégia para gerar target_url de um conjunto fixo."""
    return st.sampled_from(_TARGET_URLS)


def _st_confidence() -> st.SearchStrategy[int]:
    """Estratégia para gerar confidence entre 0 e 100."""
    return st.integers(min_value=0, max_value=100)


def _st_detected_at() -> st.SearchStrategy[datetime]:
    """Estratégia para gerar datetime dentro de um intervalo razoável."""
    # Datas entre 2024-01-01 e 2024-12-31
    min_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    max_dt = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    return st.datetimes(
        min_value=min_dt.replace(tzinfo=None),
        max_value=max_dt.replace(tzinfo=None),
    ).map(lambda dt: dt.replace(tzinfo=timezone.utc))


@st.composite
def _st_detection(draw: st.DrawFn) -> DetectionResult:
    """Estratégia composta para gerar DetectionResult aleatório."""
    return DetectionResult(
        target_url=draw(_st_target_url()),
        match_type=draw(_st_match_type()),
        confidence=draw(_st_confidence()),
        bounding_box=BoundingBox(
            x_percent=draw(st.floats(min_value=0.0, max_value=100.0)),
            y_percent=draw(st.floats(min_value=0.0, max_value=100.0)),
            width_percent=draw(
                st.floats(min_value=0.1, max_value=50.0)
            ),
            height_percent=draw(
                st.floats(min_value=0.1, max_value=50.0)
            ),
        ),
        description="Deteccao gerada por property test",
        detected_at=draw(_st_detected_at()),
        screenshot_ref_id="screenshot-prop-001",
    )


@st.composite
def _st_filter_params(draw: st.DrawFn) -> dict:
    """Estratégia para gerar combinação aleatória de filtros."""
    params: dict = {}

    # Cada filtro tem 50% de chance de ser aplicado
    if draw(st.booleans()):
        params["target_url"] = draw(_st_target_url())

    if draw(st.booleans()):
        params["match_type"] = draw(_st_match_type())

    # Datas: gerar start_date e/ou end_date
    if draw(st.booleans()):
        params["start_date"] = draw(_st_detected_at())

    if draw(st.booleans()):
        params["end_date"] = draw(_st_detected_at())

    # Garantir que start_date <= end_date quando ambos presentes
    if "start_date" in params and "end_date" in params:
        if params["start_date"] > params["end_date"]:
            params["start_date"], params["end_date"] = (
                params["end_date"],
                params["start_date"],
            )

    return params


_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
    deadline=None,
)


def _matches_filters(
    detection: DetectionResult, filters: dict
) -> bool:
    """Verifica se uma detecção casa com todos os filtros fornecidos."""
    if "target_url" in filters:
        if detection.target_url != filters["target_url"]:
            return False
    if "match_type" in filters:
        if detection.match_type != filters["match_type"]:
            return False
    if "start_date" in filters:
        if detection.detected_at < filters["start_date"]:
            return False
    if "end_date" in filters:
        if detection.detected_at > filters["end_date"]:
            return False
    return True


async def _clear_detections() -> None:
    """Remove todas as detecções do banco entre iterações do Hypothesis."""
    async with get_session() as session:
        await session.execute(delete(DetectionResultModel))


class TestQueryFilteringCorrectness:
    """Property 18: Query Filtering Correctness.

    Conjuntos de detecções + filtros, resultados respeitam todos os
    filtros aplicados e paginação.

    **Validates: Requirements 7.5**
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self):
        """Configura banco in-memory e entidades FK para cada teste."""
        config = StorageConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            detection_retention_days=90,
        )
        setup_database(config)
        await init_db()

        # Criar TargetSiteModels para satisfazer FK
        for url in _TARGET_URLS:
            async with get_session() as session:
                site = TargetSiteModel(
                    id=url,
                    url=url,
                    normalized_url=url,
                    created_at=datetime.now(timezone.utc),
                    active=True,
                )
                session.add(site)

        # Criar MonitoringCycleModel para satisfazer FK
        self._cycle_id = "cycle-prop-001"
        async with get_session() as session:
            cycle = MonitoringCycleModel(
                id=self._cycle_id,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            session.add(cycle)

        # Criar ScreenshotModel para satisfazer FK
        self._screenshot_id = "screenshot-prop-001"
        async with get_session() as session:
            screenshot = ScreenshotModel(
                id=self._screenshot_id,
                target_site_id=_TARGET_URLS[0],
                monitoring_cycle_id=self._cycle_id,
                s3_key="screenshots/cycle/prop_test.png",
                captured_at=datetime.now(timezone.utc),
                height_px=1080,
                was_truncated=False,
                expires_at=datetime.now(timezone.utc)
                + timedelta(days=90),
            )
            session.add(screenshot)

        self._store = DetectionStore(config=config)
        yield
        await close_db()

    @_PBT_SETTINGS
    @given(
        detections=st.lists(
            _st_detection(),
            min_size=1,
            max_size=20,
        ),
        filters=_st_filter_params(),
    )
    async def test_all_returned_results_match_filters(
        self,
        detections: list[DetectionResult],
        filters: dict,
    ):
        """Todos os resultados retornados devem respeitar TODOS os
        filtros aplicados simultaneamente."""
        # Limpar detecções de iterações anteriores
        await _clear_detections()

        # Salvar todas as detecções
        for det in detections:
            await self._store.save(det)

        # Consultar com filtros
        result = await self._store.query(**filters)

        # Verificar que todos os resultados casam com os filtros
        for r in result.results:
            if "target_url" in filters:
                assert r.target_url == filters["target_url"], (
                    f"Resultado com target_url={r.target_url} "
                    f"não casa com filtro={filters['target_url']}"
                )
            if "match_type" in filters:
                assert r.match_type == filters["match_type"], (
                    f"Resultado com match_type={r.match_type} "
                    f"não casa com filtro={filters['match_type']}"
                )
            if "start_date" in filters:
                det_at = r.detected_at
                if det_at.tzinfo is None:
                    det_at = det_at.replace(tzinfo=timezone.utc)
                assert det_at >= filters["start_date"], (
                    f"Resultado detected_at={det_at} anterior "
                    f"a start_date={filters['start_date']}"
                )
            if "end_date" in filters:
                det_at = r.detected_at
                if det_at.tzinfo is None:
                    det_at = det_at.replace(tzinfo=timezone.utc)
                assert det_at <= filters["end_date"], (
                    f"Resultado detected_at={det_at} posterior "
                    f"a end_date={filters['end_date']}"
                )

    @_PBT_SETTINGS
    @given(
        detections=st.lists(
            _st_detection(),
            min_size=1,
            max_size=20,
        ),
        filters=_st_filter_params(),
    )
    async def test_completeness_no_matching_results_missing(
        self,
        detections: list[DetectionResult],
        filters: dict,
    ):
        """Nenhum resultado que deveria casar com os filtros deve
        ser omitido — completude da consulta."""
        # Limpar detecções de iterações anteriores
        await _clear_detections()

        # Salvar todas as detecções
        for det in detections:
            await self._store.save(det)

        # Consultar com filtros (sem paginação limitante)
        result = await self._store.query(**filters, page_size=100)

        # Contar quantas detecções deveriam casar
        expected_count = sum(
            1 for det in detections if _matches_filters(det, filters)
        )

        assert result.total_count == expected_count, (
            f"total_count={result.total_count} diverge do "
            f"esperado={expected_count} para filtros={filters}"
        )

    @_PBT_SETTINGS
    @given(
        detections=st.lists(
            _st_detection(),
            min_size=2,
            max_size=20,
        ),
        filters=_st_filter_params(),
    )
    async def test_results_in_reverse_chronological_order(
        self,
        detections: list[DetectionResult],
        filters: dict,
    ):
        """Resultados devem ser retornados em ordem cronológica
        reversa (mais recentes primeiro)."""
        # Limpar detecções de iterações anteriores
        await _clear_detections()

        # Salvar todas as detecções
        for det in detections:
            await self._store.save(det)

        # Consultar com filtros
        result = await self._store.query(**filters, page_size=100)

        # Verificar ordenação reversa
        if len(result.results) >= 2:
            for i in range(len(result.results) - 1):
                current_dt = result.results[i].detected_at
                next_dt = result.results[i + 1].detected_at
                if current_dt.tzinfo is None:
                    current_dt = current_dt.replace(
                        tzinfo=timezone.utc
                    )
                if next_dt.tzinfo is None:
                    next_dt = next_dt.replace(tzinfo=timezone.utc)
                assert current_dt >= next_dt, (
                    f"Resultado na posição {i} "
                    f"(detected_at={current_dt}) é anterior ao "
                    f"da posição {i + 1} (detected_at={next_dt})"
                )

    @_PBT_SETTINGS
    @given(
        detections=st.lists(
            _st_detection(),
            min_size=3,
            max_size=20,
        ),
        page_size=st.integers(min_value=1, max_value=10),
    )
    async def test_pagination_page_size_respected(
        self,
        detections: list[DetectionResult],
        page_size: int,
    ):
        """Paginação deve respeitar page_size e has_next correto."""
        # Limpar detecções de iterações anteriores
        await _clear_detections()

        # Salvar todas as detecções
        for det in detections:
            await self._store.save(det)

        # Consultar página 1
        result = await self._store.query(page=1, page_size=page_size)

        # page_size deve ser respeitado
        assert len(result.results) <= page_size, (
            f"Retornou {len(result.results)} resultados, "
            f"mas page_size={page_size}"
        )

        # has_next deve refletir se há mais resultados
        total = result.total_count
        expected_has_next = total > page_size
        assert result.has_next == expected_has_next, (
            f"has_next={result.has_next} mas total={total} "
            f"e page_size={page_size}, "
            f"esperado has_next={expected_has_next}"
        )

        # Se has_next é True, a próxima página deve ter resultados
        if result.has_next:
            result_page2 = await self._store.query(
                page=2, page_size=page_size
            )
            assert len(result_page2.results) > 0, (
                "has_next=True mas página 2 está vazia"
            )
            assert len(result_page2.results) <= page_size, (
                f"Página 2 retornou {len(result_page2.results)} "
                f"resultados, mas page_size={page_size}"
            )

    @_PBT_SETTINGS
    @given(
        detections=st.lists(
            _st_detection(),
            min_size=1,
            max_size=15,
        ),
        page_size=st.integers(min_value=1, max_value=5),
    )
    async def test_pagination_all_pages_cover_total(
        self,
        detections: list[DetectionResult],
        page_size: int,
    ):
        """A soma de resultados de todas as páginas deve ser igual
        ao total_count."""
        # Limpar detecções de iterações anteriores
        await _clear_detections()

        # Salvar todas as detecções
        for det in detections:
            await self._store.save(det)

        # Iterar sobre todas as páginas
        all_results: list[DetectionResult] = []
        page = 1
        while True:
            result = await self._store.query(
                page=page, page_size=page_size
            )
            all_results.extend(result.results)
            if not result.has_next:
                break
            page += 1
            # Proteção contra loops infinitos
            if page > 100:
                break

        assert len(all_results) == result.total_count, (
            f"Soma de resultados em todas as páginas "
            f"({len(all_results)}) difere de "
            f"total_count ({result.total_count})"
        )
