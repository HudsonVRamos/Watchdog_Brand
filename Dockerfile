# Brand Watchdog - Dockerfile de produção
# Python 3.12 + Playwright + Chromium headless

FROM python:3.12-slim

# Variáveis de ambiente
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# Diretório de trabalho
WORKDIR /app

# Instala dependências Python primeiro (cache de layer)
COPY pyproject.toml ./
RUN pip install .

# Instala Playwright com Chromium e suas dependências de sistema
RUN playwright install --with-deps chromium

# Copia código da aplicação
COPY brand_watchdog/ ./brand_watchdog/
COPY config.yaml ./config.yaml

# Cria diretórios de dados
RUN mkdir -p /app/data/screenshots /app/data/logos

# Usuário não-root para segurança
RUN useradd -m -r appuser && chown -R appuser:appuser /app /opt/playwright
USER appuser

# Entry point
CMD ["python", "-m", "brand_watchdog.main"]
