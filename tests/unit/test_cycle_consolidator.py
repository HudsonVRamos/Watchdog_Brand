"""Testes unitários para CycleConsolidator.

Valida:
- Consolidação normal: todos os sites completam
- Consolidação com timeout de 60min: marca ciclo como
  "completed_with_timeout" e cria registros de falha
- Intervalo de polling configurável

Requirements: 3.1, 3.2, 3.3
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.config import WorkerConfig
from brand_watchdog.coordinator.cycle_consolidator import (
    CycleConsolidator,
)
from brand_watchdog.models.entities import (
    MonitoringCycleModel,
    SiteCycleResultModel,
)


# --- Helpers ---


def _make_config(
    poll_interval: int = 30,
    timeout_minutes: int = 60,
) -> WorkerConfig:
    """Cria WorkerConfig para testes."""
    return WorkerConfig(
        consolidation_poll_interval_seconds=poll_interval,
        consolidation_timeout_minutes=timeout_minutes,
    )


def _make_consolidator(
    poll_interval: int = 30,
    timeout_minutes: int = 60,
) -> CycleConsolidator:
    """Cria CycleConsolidator com configuração de teste."""
    config = _make_config(
        poll_interval=poll_interval,
        timeout_minutes=timeout_minutes,
    )
    return CycleConsolidator(config=config)


# --- Testes de Consolidação Normal ---


class TestConsolidateNormalCompletion:
    """Testes para consolidação quando todos sites completam."""

    @pytest.mark.asyncio
    async def test_all_sites_completed_returns_completed(self):
        """Quando todos os sites têm resultado, retorna 'completed'."""
        consolidator = _make_consolidator()

        # Mock _count_results retorna todos completos na 1ª check
        consolidator._count_results = AsyncMock(return_value=5)

        # Mock _complete_cycle retorna status
        consolidator._complete_cycle = AsyncMock(
            return_value=MonitoringCycleModel.STATUS_COMPLETED
        )

        result = await consolidator.consolidate(
            cycle_id="cycle-001",
            sites_dispatched=5,
        )

        assert result == "completed"
        consolidator._complete_cycle.assert_called_once_with(
            "cycle-001"
        )
        consolidator._count_results.assert_called_once_with(
            "cycle-001"
        )

    @pytest.mark.asyncio
    async def test_gradual_completion(self):
        """Sites completam gradualmente até atingir o total."""
        consolidator = _make_consolidator(poll_interval=0)

        # Simula: 2 resultados, depois 4, depois 5 (completo)
        consolidator._count_results = AsyncMock(
            side_effect=[2, 4, 5]
        )
        consolidator._complete_cycle = AsyncMock(
            return_value=MonitoringCycleModel.STATUS_COMPLETED
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await consolidator.consolidate(
                cycle_id="cycle-002",
                sites_dispatched=5,
            )

        assert result == "completed"
        assert consolidator._count_results.call_count == 3
        consolidator._complete_cycle.assert_called_once()


# --- Testes de Timeout 60min ---


class TestConsolidateTimeout:
    """Testes para consolidação com timeout de 60 minutos.

    Validates: Requirements 3.3
    """

    @pytest.mark.asyncio
    async def test_timeout_marks_cycle_as_completed_with_timeout(
        self,
    ):
        """Quando timeout de 60min é atingido, retorna
        'completed_with_timeout'."""
        # Usar timeout de 0 minutos para forçar timeout imediato
        consolidator = _make_consolidator(
            poll_interval=0, timeout_minutes=0
        )

        # Sites nunca completam (retorna 2 de 5)
        consolidator._count_results = AsyncMock(return_value=2)
        consolidator._timeout_cycle = AsyncMock(
            return_value=MonitoringCycleModel.STATUS_COMPLETED_WITH_TIMEOUT
        )

        result = await consolidator.consolidate(
            cycle_id="cycle-003",
            sites_dispatched=5,
        )

        assert result == "completed_with_timeout"
        consolidator._timeout_cycle.assert_called_once_with(
            "cycle-003", 5
        )

    @pytest.mark.asyncio
    async def test_timeout_creates_failure_records_for_missing_sites(
        self,
    ):
        """Timeout cria registros de falha para sites sem resultado."""
        consolidator = _make_consolidator(
            poll_interval=0, timeout_minutes=0
        )

        # Simula banco com 2 resultados existentes e 3 sites sem resultado
        mock_session = MagicMock()
        mock_session.flush = AsyncMock()

        # Resultado para subquery de sites faltantes
        missing_rows = [("site-3",), ("site-4",), ("site-5",)]
        mock_fetchall = MagicMock(return_value=missing_rows)

        # Resultado para contagem de sucesso (2)
        mock_scalar_success = MagicMock(return_value=2)
        # Resultado para contagem de falha (3 - as que vamos criar)
        mock_scalar_failed = MagicMock(return_value=3)
        # Resultado para soma de detections
        mock_scalar_detections = MagicMock(return_value=5)

        # Resultado para fetch do cycle model
        mock_cycle_model = MagicMock()
        mock_cycle_model.status = "dispatched"

        execute_results = [
            # 1: missing sites query
            MagicMock(fetchall=mock_fetchall),
            # 2: count success
            MagicMock(scalar=mock_scalar_success),
            # 3: count failed
            MagicMock(scalar=mock_scalar_failed),
            # 4: sum detections
            MagicMock(scalar=mock_scalar_detections),
            # 5: fetch cycle model
            MagicMock(scalar_one_or_none=MagicMock(
                return_value=mock_cycle_model
            )),
        ]
        mock_session.execute = AsyncMock(
            side_effect=execute_results
        )
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def mock_get_session():
            yield mock_session

        # Simular _count_results para retornar 2 (não completo)
        consolidator._count_results = AsyncMock(return_value=2)

        with patch(
            "brand_watchdog.coordinator.cycle_consolidator.get_session",
            side_effect=mock_get_session,
        ):
            result = await consolidator.consolidate(
                cycle_id="cycle-004",
                sites_dispatched=5,
            )

        assert result == "completed_with_timeout"

        # Verificar que add foi chamado para cada site faltante
        add_calls = mock_session.add.call_args_list
        assert len(add_calls) == 3

        # Verificar que os registros de falha contêm a razão correta
        for call in add_calls:
            result_model = call[0][0]
            assert isinstance(result_model, SiteCycleResultModel)
            assert result_model.status == "failure"
            assert "Timeout" in result_model.failure_reason
            assert result_model.cycle_id == "cycle-004"

        # Verificar que o ciclo foi atualizado para
        # completed_with_timeout
        assert mock_cycle_model.status == "completed_with_timeout"

    @pytest.mark.asyncio
    async def test_timeout_60min_exact_boundary(self):
        """Timeout exato em 60 minutos dispara corretamente."""
        consolidator = _make_consolidator(
            poll_interval=0, timeout_minutes=60
        )

        # Patch datetime para simular passagem de 60 minutos
        call_count = {"n": 0}

        async def mock_count_results(cycle_id):
            call_count["n"] += 1
            return 3  # Nunca atinge 5

        consolidator._count_results = mock_count_results
        consolidator._timeout_cycle = AsyncMock(
            return_value=MonitoringCycleModel.STATUS_COMPLETED_WITH_TIMEOUT
        )

        # Simular passagem de tempo: primeira chamada = t0,
        # segunda chamada = t0 + 61min (ultrapassa timeout)
        start_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        after_timeout = datetime(
            2024, 1, 1, 1, 1, 0, tzinfo=timezone.utc
        )  # +61 minutos

        datetime_call_count = {"n": 0}

        def mock_now(tz=None):
            datetime_call_count["n"] += 1
            # Primeira chamada é start_time, depois é after_timeout
            if datetime_call_count["n"] <= 1:
                return start_time
            return after_timeout

        with patch(
            "brand_watchdog.coordinator.cycle_consolidator.datetime"
        ) as mock_dt:
            mock_dt.now = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await consolidator.consolidate(
                    cycle_id="cycle-005",
                    sites_dispatched=5,
                )

        assert result == "completed_with_timeout"
        consolidator._timeout_cycle.assert_called_once()


# --- Testes de Polling Interval ---


class TestPollingInterval:
    """Testes para intervalo de polling configurável."""

    @pytest.mark.asyncio
    async def test_polls_at_configured_interval(self):
        """Polling respeita o intervalo configurado (30s)."""
        consolidator = _make_consolidator(poll_interval=30)

        # Primeira chamada: 0 resultados, segunda: 5 (completo)
        consolidator._count_results = AsyncMock(
            side_effect=[0, 5]
        )
        consolidator._complete_cycle = AsyncMock(
            return_value=MonitoringCycleModel.STATUS_COMPLETED
        )

        sleep_calls = []

        async def track_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("asyncio.sleep", side_effect=track_sleep):
            await consolidator.consolidate(
                cycle_id="cycle-006",
                sites_dispatched=5,
            )

        # Deve ter dormido 30s entre a 1ª e 2ª verificação
        assert sleep_calls == [30]

    @pytest.mark.asyncio
    async def test_custom_poll_interval(self):
        """Polling usa intervalo customizado (10s)."""
        consolidator = _make_consolidator(poll_interval=10)

        # 3 polls antes de completar
        consolidator._count_results = AsyncMock(
            side_effect=[1, 3, 5]
        )
        consolidator._complete_cycle = AsyncMock(
            return_value=MonitoringCycleModel.STATUS_COMPLETED
        )

        sleep_calls = []

        async def track_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("asyncio.sleep", side_effect=track_sleep):
            await consolidator.consolidate(
                cycle_id="cycle-007",
                sites_dispatched=5,
            )

        # 2 sleeps de 10s (entre poll 1→2 e 2→3)
        assert sleep_calls == [10, 10]
