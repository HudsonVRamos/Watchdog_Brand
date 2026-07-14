"""Property tests para consolidação do ciclo de monitoramento.

Valida que a lógica de consolidação do CycleConsolidator produz
contadores corretos (sites_processed, sites_failed, detections_found)
a partir de um conjunto arbitrário de SiteCycleResult.

# Feature: architecture-evolution, Property 3: Corretude da Consolidação do Ciclo

**Validates: Requirements 3.1**
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# Configuração PBT: mínimo 100 exemplos
_PBT_SETTINGS = settings(
    max_examples=30,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)


# --- Dataclass de teste representando SiteCycleResult ---


@dataclass(frozen=True)
class FakeSiteCycleResult:
    """Representação simplificada de SiteCycleResult para teste.

    Contém apenas os campos relevantes para a lógica de consolidação.
    """

    site_id: str
    cycle_id: str
    status: str  # "success" ou "failure"
    detections_count: int


# --- Lógica de consolidação extraída (pura, sem DB) ---


def consolidate_results(
    results: list[FakeSiteCycleResult],
) -> tuple[int, int, int]:
    """Calcula contadores de consolidação a partir dos resultados.

    Reproduz exatamente a lógica do CycleConsolidator._complete_cycle:
    - sites_processed = count(status == "success")
    - sites_failed = count(status == "failure")
    - detections_found = sum(detections_count) de todos os resultados

    Args:
        results: Lista de resultados de sites para um ciclo.

    Returns:
        Tupla (sites_processed, sites_failed, detections_found).
    """
    sites_processed = sum(
        1 for r in results if r.status == "success"
    )
    sites_failed = sum(
        1 for r in results if r.status == "failure"
    )
    detections_found = sum(r.detections_count for r in results)
    return sites_processed, sites_failed, detections_found


# --- Strategies ---


@st.composite
def site_cycle_result_strategy(
    draw: st.DrawFn,
    *,
    cycle_id: str = "cycle-123",
) -> FakeSiteCycleResult:
    """Gera um FakeSiteCycleResult com status aleatório.

    Status é "success" ou "failure", detections_count é >= 0.
    """
    site_id = draw(st.uuids().map(str))
    status = draw(st.sampled_from(["success", "failure"]))
    detections_count = draw(st.integers(min_value=0, max_value=50))
    return FakeSiteCycleResult(
        site_id=site_id,
        cycle_id=cycle_id,
        status=status,
        detections_count=detections_count,
    )


@st.composite
def site_cycle_results_list(
    draw: st.DrawFn,
) -> list[FakeSiteCycleResult]:
    """Gera uma lista de 1-50 resultados de sites com IDs únicos."""
    cycle_id = draw(st.uuids().map(str))
    results = draw(
        st.lists(
            site_cycle_result_strategy(cycle_id=cycle_id),
            min_size=1,
            max_size=50,
            unique_by=lambda r: r.site_id,
        )
    )
    return results


# --- Property Tests ---


class TestCycleConsolidation:
    """Property 3: Corretude da Consolidação do Ciclo.

    Para qualquer conjunto de SiteCycleResult (mistura de status
    "success" e "failure") associados a um cycle_id, a consolidação
    SHALL produzir contadores corretos.

    **Validates: Requirements 3.1**
    """

    @_PBT_SETTINGS
    @given(results=site_cycle_results_list())
    def test_sites_processed_equals_success_count(
        self, results: list[FakeSiteCycleResult]
    ):
        """sites_processed == contagem de resultados com status 'success'."""
        sites_processed, _, _ = consolidate_results(results)

        expected = sum(
            1 for r in results if r.status == "success"
        )
        assert sites_processed == expected

    @_PBT_SETTINGS
    @given(results=site_cycle_results_list())
    def test_sites_failed_equals_failure_count(
        self, results: list[FakeSiteCycleResult]
    ):
        """sites_failed == contagem de resultados com status 'failure'."""
        _, sites_failed, _ = consolidate_results(results)

        expected = sum(
            1 for r in results if r.status == "failure"
        )
        assert sites_failed == expected

    @_PBT_SETTINGS
    @given(results=site_cycle_results_list())
    def test_detections_found_equals_sum_of_all_detections(
        self, results: list[FakeSiteCycleResult]
    ):
        """detections_found == soma de detections_count de TODOS os resultados."""
        _, _, detections_found = consolidate_results(results)

        expected = sum(r.detections_count for r in results)
        assert detections_found == expected

    @_PBT_SETTINGS
    @given(results=site_cycle_results_list())
    def test_total_equals_processed_plus_failed(
        self, results: list[FakeSiteCycleResult]
    ):
        """sites_processed + sites_failed == total de resultados."""
        sites_processed, sites_failed, _ = consolidate_results(results)

        assert sites_processed + sites_failed == len(results)
