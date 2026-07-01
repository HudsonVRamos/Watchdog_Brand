"""Testes unitários para o entry point brand_watchdog.main.

Valida inicialização de configuração, resolução de path,
criação de email provider e fluxo geral do main().
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brand_watchdog.config import AppConfig
from brand_watchdog.main import (
    _create_email_provider,
    _resolve_config_path,
    _setup_logging,
    main,
)


class TestSetupLogging:
    """Testes para _setup_logging."""

    def test_setup_logging_no_error(self):
        """Deve configurar logging sem erros."""
        _setup_logging()


class TestResolveConfigPath:
    """Testes para _resolve_config_path."""

    def test_returns_none_when_file_not_exists(self, tmp_path, monkeypatch):
        """Retorna None quando config.yaml não existe."""
        monkeypatch.chdir(tmp_path)
        result = _resolve_config_path()
        assert result is None

    def test_returns_path_when_file_exists(self, tmp_path, monkeypatch):
        """Retorna Path quando config.yaml existe."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("schedule:\n  interval_hours: 12\n")
        monkeypatch.chdir(tmp_path)
        result = _resolve_config_path()
        assert result is not None
        assert result.exists()


class TestCreateEmailProvider:
    """Testes para _create_email_provider."""

    def test_creates_ses_provider_by_default(self):
        """Deve criar SESProvider quando provider='ses'."""
        from brand_watchdog.alerts.email_providers import SESProvider

        config = AppConfig()
        config.alert.provider = "ses"
        provider = _create_email_provider(config)
        assert isinstance(provider, SESProvider)

    def test_creates_smtp_provider(self):
        """Deve criar SMTPProvider quando provider='smtp'."""
        from brand_watchdog.alerts.email_providers import SMTPProvider

        config = AppConfig()
        config.alert.provider = "smtp"
        provider = _create_email_provider(config)
        assert isinstance(provider, SMTPProvider)


class TestMain:
    """Testes para a função main()."""

    @pytest.mark.asyncio
    async def test_main_startup_and_shutdown(
        self, tmp_path, monkeypatch
    ):
        """Testa que main() inicializa componentes e faz shutdown."""
        # Usar diretório temporário para evitar conflitos
        monkeypatch.chdir(tmp_path)

        # Criar config.yaml mínimo
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "storage:\n"
            f"  database_url: 'sqlite+aiosqlite:///{tmp_path}/test.db'\n"
            f"  screenshot_base_path: '{tmp_path}/screenshots'\n"
            "schedule:\n"
            "  interval_hours: 24\n"
        )

        # Mock do scheduler para não iniciar jobs reais
        mock_scheduler_cls = MagicMock()
        mock_scheduler_instance = MagicMock()
        mock_scheduler_instance.start = MagicMock()
        mock_scheduler_instance.stop = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler_instance

        # Simular shutdown imediato via evento
        original_event_wait = asyncio.Event.wait

        async def fake_wait(self):
            """Simula recebimento de sinal imediato."""
            self.set()

        with patch(
            "brand_watchdog.main.asyncio.Event.wait",
            fake_wait,
        ), patch(
            "brand_watchdog.scheduler.scheduler.AsyncIOScheduler"
        ):
            # Executar main
            await main()

        # Verificar que o banco foi criado
        # (init_db cria as tabelas)
        assert (tmp_path / "test.db").exists()
