"""Configuração compartilhada de testes do Brand Watchdog."""

import pytest


@pytest.fixture
def anyio_backend():
    """Backend padrão para testes assíncronos."""
    return "asyncio"
