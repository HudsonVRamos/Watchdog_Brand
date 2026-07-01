# 🔍 Brand Watchdog

Sistema automatizado de monitoramento de compliance e uso de marca em websites externos, construído com Python, Playwright, AWS Bedrock (Claude) e infraestrutura serverless na AWS.

---

## 📋 Sumário

- [Visão Geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Funcionalidades](#funcionalidades)
- [Pré-requisitos](#pré-requisitos)
- [Instalação Local](#instalação-local)
- [Configuração](#configuração)
- [Uso](#uso)
- [Deploy em Produção](#deploy-em-produção)
- [Testes](#testes)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Infraestrutura AWS](#infraestrutura-aws)
- [Custos Estimados](#custos-estimados)
- [Roadmap](#roadmap)

---

## Visão Geral

O **Brand Watchdog** resolve o problema de monitorar automaticamente o uso de marcas em sites de terceiros. Proprietários de marca precisam detectar quando seus logotipos ou nomes são usados sem autorização (ou fora de compliance) em websites externos — uma tarefa inviável manualmente em escala.

### O que faz

1. **Captura** screenshots full-page de sites configurados usando Playwright + Chromium
2. **Analisa** cada screenshot com IA multimodal (Claude Sonnet via AWS Bedrock)
3. **Detecta** logotipos e menções textuais de marca, com localização e nível de confiança
4. **Valida compliance** de regras de parceria (ex: SKY+/Amazon Prime)
5. **Notifica** por email com relatórios estruturados de violações encontradas
6. **Persiste** resultados para histórico e análise de tendências

### Módulos de Análise

| Módulo | Descrição | Status |
|--------|-----------|--------|
| **Brand Detection** | Detecta presença de logos e textos de marca | ✅ Produção |
| **Compliance Validation** | Valida regras da parceria SKY+/Amazon Prime | 🚧 MVP.1 |

---

## Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                         ECS Fargate (24/7)                        │
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │Scheduler │→ │Coordinator│→ │ Crawler  │→ │   Analyzer    │   │
│  │(APSched) │  │  (ciclo) │  │(Playwright)│ │(Bedrock/Claude)│  │
│  └──────────┘  └────┬─────┘  └──────────┘  └───────┬───────┘   │
│                      │                               │           │
│                      ▼                               ▼           │
│              ┌──────────────┐                ┌─────────────┐     │
│              │  Email Alert │                │  Detection  │     │
│              │   (AWS SES)  │                │   Store     │     │
│              └──────────────┘                └──────┬──────┘     │
└─────────────────────────────────────────────────────┼────────────┘
                                                      │
                                               ┌──────▼──────┐
                                               │Aurora Postgres│
                                               │  (Serverless) │
                                               └──────────────┘
```

**Stack principal:**
- Python 3.12 + asyncio
- Playwright (Chromium headless) para crawling
- AWS Bedrock (Claude Sonnet 4.6) para análise visual
- Aurora PostgreSQL Serverless v2 para persistência
- AWS SES para notificações por email
- ECS Fargate para execução 24/7
- CloudFormation para IaC

---

## Funcionalidades

### Monitoramento de Marca
- Captura screenshots full-page (até 20.000px de altura)
- Detecta logos mesmo redimensionados, rotacionados ou recoloridos
- Detecta menções textuais em qualquer fonte, tamanho e contexto
- Suporta até 200 sites-alvo simultâneos
- Ciclos configuráveis (1 a 720 horas)

### Compliance (MVP.1 - SKY+/Amazon Prime)
- Validação de 6 regras de compliance por screenshot
- Comparação visual com imagens de referência oficiais
- Relatório pass/fail por regra individual
- Verificação de logos, nomenclatura, preços e separação visual

### Alertas e Relatórios
- Email consolidado por site por ciclo
- Supressão de duplicatas
- Retry automático com backoff exponencial
- Suporte a múltiplos destinatários

### Persistência
- Histórico completo de detecções com retenção configurável
- Expiração automática de dados antigos
- Bounding boxes para localização visual das detecções

---

## Pré-requisitos

### Desenvolvimento Local

| Requisito | Versão Mínima | Propósito |
|-----------|---------------|-----------|
| Python | 3.10+ | Runtime |
| Docker | 20.10+ | Container + PostgreSQL local |
| Docker Compose | 2.0+ | Orquestração local |
| AWS CLI | 2.x | Deploy e administração |
| Git | 2.x | Controle de versão |

### Serviços AWS (Produção)

- Conta AWS com acesso a Bedrock (Claude Sonnet)
- SES com sender email verificado
- Permissões para criar VPC, ECS, RDS, S3, ECR

---

## Instalação Local

### 1. Clonar o repositório

```bash
git clone https://github.com/HudsonVRamos/Watchdog_Brand.git
cd Watchdog_Brand
```

### 2. Criar ambiente virtual

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate
```

### 3. Instalar dependências

```bash
# Produção
pip install -e .

# Desenvolvimento (inclui pytest, hypothesis, coverage)
pip install -e ".[dev]"

# Instalar browser do Playwright
playwright install chromium
```

### 4. Subir banco de dados local (Docker)

```bash
docker-compose up -d db
```

### 5. Configurar variáveis de ambiente

```bash
# Mínimo para desenvolvimento local
export BRAND_WATCHDOG_STORAGE_DATABASE_URL="postgresql+asyncpg://watchdog:watchdog123@localhost:5432/brand_watchdog"
export BRAND_WATCHDOG_ALERT_SES_SENDER="dev@localhost"
export BRAND_WATCHDOG_ALERT_RECIPIENTS="dev@localhost"
export AWS_DEFAULT_REGION="us-east-1"
```

### 6. Executar

```bash
# Aplicação completa (scheduler + monitoramento)
python -m brand_watchdog.main

# Apenas CLI (administração)
python -m brand_watchdog.cli --help
```

---

## Configuração

### Arquivo `config.yaml`

```yaml
crawler:
  viewport_width: 1280            # Largura do viewport
  page_timeout_seconds: 60        # Timeout por página
  network_idle_timeout_ms: 500    # Network idle threshold
  max_screenshot_height_px: 8000  # Altura máxima do screenshot

analyzer:
  bedrock_model_id: "us.anthropic.claude-sonnet-4-6"
  bedrock_region: "us-east-1"
  confidence_threshold: 70        # Mínimo para alertar (0-100)
  request_timeout_seconds: 60
  max_retries: 3
  retry_base_delay_seconds: 2.0

alert:
  provider: "ses"                 # "ses" ou "smtp"
  ses_region: "us-east-1"
  retry_attempts: 3
  retry_interval_seconds: 30

schedule:
  interval_hours: 24              # Frequência dos ciclos (1-720h)

storage:
  screenshot_retention_days: 90   # Retenção de screenshots
  detection_retention_days: 90    # Retenção de detecções
  screenshot_base_path: "/app/data/screenshots"

max_target_sites: 200             # Limite de sites monitorados
```

### Variáveis de Ambiente

Todas as configurações podem ser sobrescritas via variáveis de ambiente com o prefixo `BRAND_WATCHDOG_`:

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `BRAND_WATCHDOG_STORAGE_DATABASE_URL` | Connection string PostgreSQL | `postgresql+asyncpg://user:pass@host:5432/db` |
| `BRAND_WATCHDOG_ALERT_SES_SENDER` | Email remetente (verificado no SES) | `alerts@empresa.com` |
| `BRAND_WATCHDOG_ALERT_RECIPIENTS` | Destinatários (separados por vírgula) | `a@x.com,b@x.com` |
| `BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS` | Intervalo entre ciclos | `24` |
| `BRAND_WATCHDOG_ANALYZER_BEDROCK_REGION` | Região do Bedrock | `us-east-1` |

---

## Uso

### CLI — Gerenciamento de Sites

```bash
# Adicionar site para monitoramento
python -m brand_watchdog.cli add-site https://www.exemplo.com.br

# Listar sites monitorados
python -m brand_watchdog.cli list-sites

# Remover site
python -m brand_watchdog.cli remove-site <site-id>
```

### CLI — Gerenciamento de Marcas

```bash
# Adicionar marca textual para detectar
python -m brand_watchdog.cli add-text "SKY+"

# Adicionar logo para detectar
python -m brand_watchdog.cli add-logo path/to/logo.png

# Listar assets de marca
python -m brand_watchdog.cli list-assets
```

### CLI — Execução Manual

```bash
# Disparar ciclo de monitoramento imediatamente
python -m brand_watchdog.cli run-cycle
```

### Docker Compose (Ambiente Completo)

```bash
# Subir aplicação + banco
docker-compose up -d

# Ver logs
docker-compose logs -f app

# Parar tudo
docker-compose down
```

---

## Deploy em Produção

### Infraestrutura (primeira vez)

A infra é gerenciada via CloudFormation:

```powershell
# Criar toda a infra AWS
.\infra\deploy.ps1
```

Isso cria: VPC, subnets, ECS cluster, RDS Aurora, S3, ECR, IAM roles, security groups.

### Deploy de Código (atualizações)

```powershell
# 1. Criar pacote source
Remove-Item source.zip -Force
tar -acf source.zip -C . Dockerfile .dockerignore buildspec.yml config.yaml pyproject.toml brand_watchdog watchdog_rules

# 2. Upload para S3
aws s3 cp source.zip s3://brand-watchdog-build-761018874615/source.zip --region us-east-1

# 3. Build da imagem Docker na nuvem
aws codebuild start-build --project-name brand-watchdog-build --region us-east-1

# 4. Aguardar conclusão (~2-3 min) e forçar redeploy
aws ecs update-service --cluster brand-watchdog-cluster --service brand-watchdog-service --force-new-deployment --region us-east-1
```

### Monitoramento

```powershell
# Logs em tempo real
aws logs tail /ecs/brand-watchdog --follow --region us-east-1

# Status do serviço
aws ecs describe-services --cluster brand-watchdog-cluster --services brand-watchdog-service --region us-east-1
```

### Destruição (encerrar custos)

```powershell
.\infra\destroy.ps1
# Confirmar digitando "DESTRUIR"
```

---

## Testes

### Executar Todos os Testes

```bash
python -m pytest tests/ -v
```

### Por Categoria

```bash
# Unit tests (~283 testes, ~20s)
python -m pytest tests/unit/ -v

# Property-based tests com Hypothesis (~93 testes, ~40s)
python -m pytest tests/property/ -v

# Integration tests (~41 testes, ~7s)
python -m pytest tests/integration/ -v
```

### Com Cobertura

```bash
python -m pytest tests/ --cov=brand_watchdog --cov-report=html
# Abrir htmlcov/index.html no navegador
```

### Resumo de Cobertura

| Tipo | Quantidade | Tempo | Framework |
|------|-----------|-------|-----------|
| Unit tests | 283 | ~20s | pytest |
| Property tests | 93 | ~40s | Hypothesis |
| Integration tests | 41 | ~7s | pytest-asyncio |
| **Total** | **417** | **~67s** | |

---

## Estrutura do Projeto

```
brand_watchdog/
├── main.py                    # Entry point + graceful shutdown
├── config.py                  # Config YAML + env vars
├── cli.py                     # CLI de administração
├── models/
│   ├── database.py            # SQLAlchemy engine + sessions
│   ├── entities.py            # 6 modelos ORM
│   └── dataclasses.py         # DTOs de domínio
├── crawler/
│   └── crawler.py             # Playwright + Chromium headless
├── analyzer/
│   ├── analyzer.py            # Orquestração de análise (brand detection)
│   └── bedrock_client.py      # Client AWS Bedrock (Claude)
├── alerts/
│   ├── alert_service.py       # Alertas + supressão de duplicatas
│   └── email_providers.py     # SES + SMTP providers
├── registry/
│   ├── brand_registry.py      # CRUD de brand assets
│   └── target_site_manager.py # CRUD de target sites
├── storage/
│   ├── screenshot_store.py    # Persistência de screenshots
│   └── detection_store.py     # Persistência de detecções
├── coordinator/
│   └── coordinator.py         # Orquestração do ciclo completo
├── scheduler/
│   └── scheduler.py           # APScheduler wrapper
└── utils/
    ├── validators.py          # URL + asset validation
    ├── hashing.py             # SHA-256 deduplicação
    └── retry.py               # Helpers de retry

tests/
├── unit/                      # Testes unitários
├── property/                  # Property-based tests (Hypothesis)
└── integration/               # Testes de integração

infra/
├── cloudformation.yml         # IaC completa
├── deploy.ps1                 # Script de deploy (Windows)
├── deploy.sh                  # Script de deploy (Linux)
├── destroy.ps1                # Destruição de infra (Windows)
└── destroy.sh                 # Destruição de infra (Linux)

watchdog_rules/
└── SKY_Amazon_Imagens/        # Imagens de referência para compliance
    ├── Artes_aprovadas_referencia.PNG
    ├── Logo_errado_logo_correto.PNG
    └── logo_sky_plus_amazon.PNG

docs/
└── ARCHITECTURE.md            # Documentação técnica detalhada
```

---

## Infraestrutura AWS

### Recursos Provisionados

| Recurso | Tipo | Propósito |
|---------|------|-----------|
| VPC (10.0.0.0/16) | Rede isolada | Segurança de rede |
| 2 Public Subnets | NAT Gateway | Acesso internet |
| 2 Private Subnets | ECS + RDS | Isolamento |
| ECS Fargate | Container 24/7 | Execução da aplicação |
| Aurora PostgreSQL Serverless v2 | Banco de dados | Persistência |
| S3 | Object storage | Screenshots |
| ECR | Container registry | Imagens Docker |
| CodeBuild | CI/CD | Build automatizado |
| CloudWatch Logs | Logging | Observabilidade |
| SES | Email | Notificações |

### Segurança

- ECS tasks em subnets privadas (sem IP público)
- RDS acessível apenas pelo Security Group do ECS
- IAM com princípio de menor privilégio
- Container roda como usuário non-root
- Sem access keys — usa IAM roles

---

## Custos Estimados

| Recurso | Custo/mês |
|---------|-----------|
| ECS Fargate (1 vCPU, 4GB) | ~$42 |
| Aurora Serverless v2 (0.5 ACU) | ~$44 |
| NAT Gateway | ~$33 |
| Bedrock (Claude Sonnet) | ~$5-15 |
| CloudWatch Logs | ~$2.50 |
| S3 + ECR + SES | ~$1 |
| **Total** | **~$128-148** |

---

## Roadmap

- [x] Monitoramento de marca textual
- [x] Captura full-page com Playwright
- [x] Análise com Claude Sonnet (Bedrock)
- [x] Alertas por email (SES)
- [x] 417 testes (unit + property + integration)
- [x] Deploy automatizado (CodeBuild + ECS)
- [ ] **MVP.1**: Compliance SKY+/Amazon Prime (6 regras)
- [ ] API HTTP (FastAPI)
- [ ] Dashboard web
- [ ] Detecção visual de logos por comparação de imagem
- [ ] Multi-tenant
- [ ] Export PDF/CSV

---

## Licença

Uso interno — projeto proprietário.

---

## Autor

**Hudson Ventura Ramos**  
hudson.venturaramos@sky.com.br
