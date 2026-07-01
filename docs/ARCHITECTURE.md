# Brand Watchdog — Documentação Técnica Completa

## 1. Visão Geral

**Brand Watchdog** é um sistema automatizado de monitoramento que detecta uso não autorizado de ativos de marca (logotipos e menções textuais) em websites externos. O sistema opera em ciclos periódicos, capturando screenshots full-page de sites configurados e analisando-os com IA multimodal para identificar violações de marca.

### 1.1 Problema Resolvido

Proprietários de marca precisam detectar quando seus logotipos ou nomes são usados sem autorização em sites de terceiros. A verificação manual é inviável em escala. O Brand Watchdog automatiza esse processo com crawling, análise por IA e alertas por email.

### 1.2 Capacidades

- Monitoramento de até 200 sites-alvo simultâneos
- Captura full-page com Playwright (incluindo conteúdo lazy-loaded)
- Análise multimodal via Claude Sonnet 4.6 (AWS Bedrock)
- Detecção de logos (mesmo redimensionados, rotacionados, recoloridos)
- Detecção de texto de marca (qualquer fonte, tamanho, contexto)
- Alertas por email (AWS SES) com supressão de duplicatas
- Retenção configurável de screenshots e resultados (1-365 dias)
- Agendamento flexível (1-720 horas entre ciclos)

---

## 2. Arquitetura de Infraestrutura AWS

### 2.1 Diagrama Geral

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Account: 761018874615                       │
│                              Region: us-east-1 (Virginia)                    │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  VPC: brand-watchdog-vpc (10.0.0.0/16)                                │ │
│  │                                                                        │ │
│  │  ┌─────────────────────┐    ┌─────────────────────┐                   │ │
│  │  │  Public Subnet 1    │    │  Public Subnet 2    │                   │ │
│  │  │  10.0.1.0/24        │    │  10.0.2.0/24        │                   │ │
│  │  │  AZ: us-east-1a     │    │  AZ: us-east-1b     │                   │ │
│  │  │                     │    │                     │                   │ │
│  │  │  ┌───────────────┐  │    │                     │                   │ │
│  │  │  │  NAT Gateway  │  │    │                     │                   │ │
│  │  │  │  + Elastic IP  │  │    │                     │                   │ │
│  │  │  └───────┬───────┘  │    │                     │                   │ │
│  │  └──────────┼──────────┘    └─────────────────────┘                   │ │
│  │             │                                                          │ │
│  │  ┌──────────┼──────────┐    ┌─────────────────────┐                   │ │
│  │  │  Private Subnet 1   │    │  Private Subnet 2   │                   │ │
│  │  │  10.0.10.0/24       │    │  10.0.11.0/24       │                   │ │
│  │  │  AZ: us-east-1a     │    │  AZ: us-east-1b     │                   │ │
│  │  │                     │    │                     │                   │ │
│  │  │  ┌───────────────┐  │    │  ┌───────────────┐  │                   │ │
│  │  │  │  ECS Fargate   │  │    │  │  RDS Aurora   │  │                   │ │
│  │  │  │  Task          │  │    │  │  PostgreSQL   │  │                   │ │
│  │  │  └───────────────┘  │    │  └───────────────┘  │                   │ │
│  │  └─────────────────────┘    └─────────────────────┘                   │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │     ECR      │  │      S3      │  │   Bedrock    │  │     SES       │  │
│  │  Container   │  │ Screenshots  │  │  Claude 4.6  │  │   Emails      │  │
│  │  Registry    │  │  Bucket      │  │  Sonnet      │  │               │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └───────────────┘  │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                     │
│  │  CloudWatch  │  │  CodeBuild   │  │     IAM      │                     │
│  │  Logs        │  │  CI/CD       │  │   Roles      │                     │
│  └──────────────┘  └──────────────┘  └──────────────┘                     │
└─────────────────────────────────────────────────────────────────────────────┘
```


### 2.2 Inventário de Recursos AWS

| Recurso | Nome/Identificador | Tipo | Propósito |
|---------|-------------------|------|-----------|
| VPC | `brand-watchdog-vpc` | VPC | Isolamento de rede |
| Public Subnet 1 | `brand-watchdog-public-1` | Subnet (10.0.1.0/24, us-east-1a) | NAT Gateway |
| Public Subnet 2 | `brand-watchdog-public-2` | Subnet (10.0.2.0/24, us-east-1b) | Redundância |
| Private Subnet 1 | `brand-watchdog-private-1` | Subnet (10.0.10.0/24, us-east-1a) | ECS Tasks |
| Private Subnet 2 | `brand-watchdog-private-2` | Subnet (10.0.11.0/24, us-east-1b) | RDS (Multi-AZ) |
| Internet Gateway | `brand-watchdog-igw` | IGW | Acesso internet (public subnets) |
| NAT Gateway | `brand-watchdog-nat` | NAT GW | Internet para private subnets |
| Elastic IP | `brand-watchdog-nat-eip` | EIP | IP fixo do NAT |
| ECS Cluster | `brand-watchdog-cluster` | ECS Cluster | Orquestração de containers |
| ECS Service | `brand-watchdog-service` | ECS Service (Fargate) | Mantém 1 task rodando 24/7 |
| Task Definition | `brand-watchdog` | ECS Task Def | Spec do container |
| ECR Repository | `brand-watchdog` | ECR | Imagens Docker |
| RDS Cluster | `brand-watchdog-cluster` | Aurora PostgreSQL Serverless v2 | Banco de dados |
| RDS Instance | `brand-watchdog-instance-1` | db.serverless | Instância do banco |
| S3 Bucket | `brand-watchdog-screenshots-761018874615` | S3 | Screenshots capturados |
| Log Group | `/ecs/brand-watchdog` | CloudWatch Logs | Logs da aplicação |
| Security Group (ECS) | `brand-watchdog-ecs-sg` | SG | Regras de rede ECS |
| Security Group (RDS) | `brand-watchdog-rds-sg` | SG | Regras de rede RDS |
| IAM Role (Execution) | `brand-watchdog-ecs-execution-role` | IAM Role | Pull de imagens ECR + logs |
| IAM Role (Task) | `brand-watchdog-ecs-task-role` | IAM Role | Bedrock + SES + S3 |
| IAM Role (CodeBuild) | `brand-watchdog-codebuild-role` | IAM Role | Build + push ECR |
| CodeBuild Project | `brand-watchdog-build` | CodeBuild | CI/CD (build Docker na nuvem) |
| S3 (Build) | `brand-watchdog-build-761018874615` | S3 | Source code para CodeBuild |
| CloudFormation Stack | `brand-watchdog-stack` | CFN Stack | IaC (toda a infra) |


### 2.3 Nota sobre Lambda

Este projeto **não utiliza AWS Lambda**. A decisão foi intencional:

- **Playwright requer container com Chromium** (~1.2GB), incompatível com os limites de Lambda (250MB deploy, 10GB container com cold start de 30s+)
- **Ciclos de monitoramento são long-running** (2-5 minutos por site), excedendo o timeout ideal de Lambda
- **APScheduler é um processo contínuo**, incompatível com o modelo event-driven de Lambda
- **ECS Fargate** é a escolha correta para workloads containerizados long-running

---

## 3. Fluxo de Execução

### 3.1 Ciclo de Monitoramento (Fluxo Principal)

```mermaid
sequenceDiagram
    participant SCH as APScheduler
    participant COORD as Coordinator
    participant CRAWL as Crawler (Playwright)
    participant STORE_SS as Screenshot Store
    participant BEDROCK as AWS Bedrock (Claude 4.6)
    participant STORE_DET as Detection Store
    participant ALERT as Alert Service
    participant SES as AWS SES
    participant DB as Aurora PostgreSQL

    SCH->>COORD: trigger run_cycle() (cada 24h)
    COORD->>DB: CREATE monitoring_cycle (status=running)
    
    loop Para cada Target Site
        COORD->>CRAWL: capture(url)
        CRAWL->>CRAWL: Chromium: navigate + scroll + wait networkidle
        CRAWL-->>COORD: CaptureResult (screenshot PNG, 9181px)
        
        COORD->>STORE_SS: store(png_bytes, site_id, cycle_id)
        STORE_SS->>DB: INSERT screenshots (id, path, expires_at)
        STORE_SS-->>COORD: ScreenshotModel (id=UUID)
        
        COORD->>BEDROCK: analyze(screenshot + brand_assets)
        BEDROCK-->>COORD: DetectionResults[] (10 detecções, 88-97% conf)
        
        loop Para cada detecção
            COORD->>STORE_DET: save(detection, site_id, cycle_id)
            STORE_DET->>DB: INSERT detection_results
            
            alt confidence >= 70 (threshold)
                COORD->>ALERT: send_alert(detection, recipients)
                ALERT->>ALERT: check_duplicate_suppression()
                ALERT->>SES: send_email(subject, body)
                SES-->>ALERT: 200 OK
            end
        end
    end
    
    COORD->>DB: UPDATE monitoring_cycle (status=completed, stats)
```


### 3.2 Fluxo de Deploy (CI/CD)

```mermaid
sequenceDiagram
    participant DEV as Desenvolvedor
    participant S3 as S3 (source.zip)
    participant CB as CodeBuild
    participant ECR as ECR Repository
    participant ECS as ECS Service

    DEV->>DEV: tar -acf source.zip (código)
    DEV->>S3: aws s3 cp source.zip
    DEV->>CB: aws codebuild start-build
    CB->>S3: Download source.zip
    CB->>CB: docker build (Python + Playwright + Chromium)
    CB->>ECR: docker push brand-watchdog:latest
    DEV->>ECS: aws ecs update-service --force-new-deployment
    ECS->>ECR: Pull nova imagem
    ECS->>ECS: Start new task, stop old task
```

### 3.3 Fluxo de Administração (CLI)

```
┌─────────────────────────────────────────────────────────────┐
│  Comandos CLI (via ECS run-task com override)               │
│                                                             │
│  add-site <url>     → Registra site para monitoramento      │
│  remove-site <id>   → Remove site da lista                  │
│  list-sites         → Lista todos os sites registrados      │
│  add-text <texto>   → Registra marca textual                │
│  add-logo <path>    → Registra logo (imagem)                │
│  list-assets        → Lista brand assets registrados        │
│  run-cycle          → Dispara ciclo manual imediatamente    │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Componentes de Software

### 4.1 Diagrama de Componentes

```
brand_watchdog/
├── main.py                    ← Entry point + signal handling
├── config.py                  ← Configuração YAML + env vars
├── cli.py                     ← CLI de administração
├── models/
│   ├── database.py            ← SQLAlchemy engine + session
│   ├── entities.py            ← 6 modelos ORM (tabelas)
│   └── dataclasses.py         ← DTOs de domínio
├── crawler/
│   └── crawler.py             ← Playwright + Chromium headless
├── analyzer/
│   ├── analyzer.py            ← Orquestração de análise
│   └── bedrock_client.py      ← Client AWS Bedrock (Claude)
├── alerts/
│   ├── alert_service.py       ← Lógica de alertas + supressão
│   └── email_providers.py     ← SES + SMTP providers
├── registry/
│   ├── brand_registry.py      ← CRUD de brand assets
│   └── target_site_manager.py ← CRUD de target sites
├── storage/
│   ├── screenshot_store.py    ← Persistência de screenshots
│   └── detection_store.py     ← Persistência de detecções
├── coordinator/
│   └── coordinator.py         ← Orquestração do ciclo completo
├── scheduler/
│   └── scheduler.py           ← APScheduler wrapper
└── utils/
    ├── validators.py          ← URL + asset validation
    ├── hashing.py             ← SHA-256 para deduplicação
    └── retry.py               ← Helpers de retry
```


### 4.2 Responsabilidades por Componente

| Componente | Responsabilidade | Dependências |
|------------|-----------------|--------------|
| `main.py` | Inicialização, DI, signal handling, graceful shutdown | Todos |
| `config.py` | Carrega YAML + env vars, valida, expõe AppConfig | PyYAML |
| `cli.py` | Comandos administrativos (add-site, run-cycle, etc.) | Todos |
| `Crawler` | Navega em sites, scroll, network-idle, screenshot full-page | Playwright |
| `Analyzer` | Constrói prompt, parseia resposta, filtra por confidence | BedrockClient |
| `BedrockClient` | Invoca Claude via API Bedrock, retry, extrai JSON | boto3, tenacity |
| `AlertService` | Envia emails, suprime duplicatas, formata conteúdo | EmailProvider, DetectionStore |
| `SESProvider` | Envio via AWS SES | boto3 |
| `SMTPProvider` | Envio via SMTP (fallback) | aiosmtplib |
| `BrandRegistry` | CRUD de logos e textos, deduplicação por hash | SQLAlchemy |
| `TargetSiteManager` | CRUD de sites, validação URL, limite de 200 | URLValidator |
| `DetectionStore` | Persistência de detecções, query, cleanup expirados | SQLAlchemy, tenacity |
| `ScreenshotStore` | Salva PNG no filesystem, metadados no banco, cleanup | SQLAlchemy, tenacity |
| `MonitoringCoordinator` | Orquestra ciclo: capture → analyze → alert → persist | Todos |
| `MonitoringScheduler` | APScheduler wrapper, intervalo configurável, cleanup job | APScheduler |
| `URLValidator` | Valida URL (scheme, hostname RFC 1123, max 2048 chars) | urllib |

---

## 5. Modelo de Dados

### 5.1 Diagrama ER

```mermaid
erDiagram
    TARGET_SITE {
        varchar id PK "UUID"
        varchar url "URL original (max 2048)"
        varchar normalized_url UK "Lowercase, sem trailing slash"
        timestamp created_at "UTC"
        boolean active "default: true"
    }
    
    BRAND_ASSET {
        varchar id PK "UUID"
        varchar asset_type "logo | text"
        varchar file_path "Path do logo no filesystem"
        varchar text_value "Texto da marca (2-256 chars)"
        varchar content_hash UK "SHA-256 para deduplicação"
        varchar original_filename "Nome original do arquivo"
        integer file_size_bytes "Tamanho em bytes"
        timestamp created_at "UTC"
    }
    
    MONITORING_CYCLE {
        varchar id PK "UUID"
        timestamp started_at "Início do ciclo"
        timestamp ended_at "Fim do ciclo"
        integer sites_processed "Sucesso"
        integer sites_failed "Falhas"
        integer detections_found "Total detecções"
        varchar status "running | completed | skipped"
    }
    
    SCREENSHOT {
        varchar id PK "UUID"
        varchar target_site_id FK "→ target_sites.id"
        varchar monitoring_cycle_id FK "→ monitoring_cycles.id"
        varchar file_path "Caminho no filesystem"
        timestamp captured_at "UTC (segundo)"
        integer height_px "Altura em pixels"
        boolean was_truncated "Truncado em 20000px?"
        timestamp expires_at "Data de expiração"
    }
    
    DETECTION_RESULT {
        varchar id PK "UUID"
        varchar target_site_id FK "→ target_sites.id"
        varchar screenshot_id FK "→ screenshots.id"
        varchar monitoring_cycle_id FK "→ monitoring_cycles.id"
        varchar match_type "logo | text"
        integer confidence "0-100"
        float bbox_x_percent "Posição X (%)"
        float bbox_y_percent "Posição Y (%)"
        float bbox_width_percent "Largura (%)"
        float bbox_height_percent "Altura (%)"
        varchar description "Descrição da detecção"
        timestamp detected_at "UTC"
        timestamp expires_at "Expiração (retention)"
    }
    
    ALERT_LOG {
        varchar id PK "UUID"
        varchar detection_result_id FK "→ detection_results.id"
        varchar recipient "Email destinatário"
        timestamp sent_at "UTC"
        boolean success "Envio bem-sucedido?"
        varchar error_message "Erro (se falhou)"
    }
    
    TARGET_SITE ||--o{ SCREENSHOT : "captures"
    TARGET_SITE ||--o{ DETECTION_RESULT : "has"
    MONITORING_CYCLE ||--o{ SCREENSHOT : "produces"
    MONITORING_CYCLE ||--o{ DETECTION_RESULT : "contains"
    SCREENSHOT ||--o{ DETECTION_RESULT : "references"
    DETECTION_RESULT ||--o{ ALERT_LOG : "triggers"
```


### 5.2 Índices e Constraints

| Tabela | Coluna | Tipo | Propósito |
|--------|--------|------|-----------|
| `target_sites` | `normalized_url` | UNIQUE | Previne duplicatas |
| `brand_assets` | `content_hash` | UNIQUE | Deduplicação por conteúdo |
| `screenshots` | `expires_at` | INDEX | Cleanup eficiente |
| `detection_results` | `expires_at` | INDEX | Cleanup eficiente |
| `detection_results` | `target_site_id` | FK | Integridade referencial |
| `detection_results` | `screenshot_id` | FK | Integridade referencial |
| `detection_results` | `monitoring_cycle_id` | FK | Integridade referencial |

---

## 6. Segurança

### 6.1 Rede

- **VPC isolada** (10.0.0.0/16) com subnets públicas e privadas
- **ECS tasks em subnets privadas** — sem IP público, acesso via NAT Gateway
- **RDS em subnet privada** — acessível apenas pelo Security Group do ECS
- **Security Group do RDS** — aceita conexões apenas na porta 5432 vindo do SG do ECS
- **Security Group do ECS** — permite apenas tráfego de saída (egress all)

### 6.2 IAM (Princípio de Menor Privilégio)

| Role | Permissões | Escopo |
|------|-----------|--------|
| `brand-watchdog-ecs-execution-role` | ECR pull, CloudWatch Logs, Secrets Manager read | Apenas recursos do projeto |
| `brand-watchdog-ecs-task-role` | Bedrock InvokeModel, SES SendEmail, S3 CRUD no bucket específico | Recursos específicos |
| `brand-watchdog-codebuild-role` | ECR push, S3 read (source), CloudWatch Logs | Apenas build |

### 6.3 Dados Sensíveis

| Dado | Armazenamento | Acesso |
|------|--------------|--------|
| DB Password | Parâmetro CloudFormation (NoEcho) | Env var no container |
| AWS Credentials | IAM Role (não há access keys) | Automático via metadata |
| Email Recipients | Env var | Configurável via CloudFormation |
| Screenshots | Filesystem no container + S3 (lifecycle 90 dias) | Task Role |

---

## 7. Custos Detalhados

### 7.1 Breakdown Mensal (us-east-1, Julho 2026)

| Recurso | Especificação | Cálculo | Custo/mês |
|---------|--------------|---------|-----------|
| **ECS Fargate** | 1 vCPU, 4GB RAM, 24/7 | 730h × ($0.04048/vCPU-h + $0.004445/GB-h × 4) | **~$42.50** |
| **NAT Gateway** | 1 gateway + dados | $0.045/h × 730h + ~5GB × $0.045/GB | **~$33.10** |
| **RDS Aurora Serverless v2** | 0.5 ACU mínimo, PostgreSQL 16.6 | 0.5 ACU × $0.12/ACU-h × 730h | **~$43.80** |
| **S3** | ~10GB screenshots, lifecycle 90d | 10GB × $0.023/GB + requests | **~$0.50** |
| **CloudWatch Logs** | ~5GB/mês ingestão | 5GB × $0.50/GB | **~$2.50** |
| **ECR** | ~1.5GB imagens (5 versões) | 1.5GB × $0.10/GB | **~$0.15** |
| **AWS Bedrock (Claude Sonnet 4.6)** | ~200 sites/mês × 1 screenshot cada | Input: ~3MB imagem/call, ~$0.003/1K input tokens | **~$5-15** |
| **AWS SES** | ~200 emails/mês | $0.10/1000 emails | **~$0.02** |
| **CodeBuild** | ~10 builds/mês × 3min cada | build.general1.medium: $0.005/min | **~$0.15** |
| | | **TOTAL ESTIMADO** | **~$128-148/mês** |

### 7.2 Variáveis de Custo

- **Bedrock**: Custo proporcional ao número de sites e frequência. 200 sites × 1 call/dia = ~6000 calls/mês
- **NAT Gateway**: Custo fixo alto. Alternativa mais barata: VPC Endpoints para S3/ECR/Bedrock (~$7/cada)
- **RDS**: Escala para zero em períodos ociosos (mas leva ~30s para retomar)

### 7.3 Otimizações Possíveis (Futuro)

| Otimização | Economia | Trade-off |
|-----------|----------|-----------|
| VPC Endpoints (S3, ECR) | -$10/mês no NAT | Custo fixo dos endpoints |
| Fargate Spot | -30-70% no ECS | Possível interrupção |
| RDS com auto-pause | -50% no RDS | Cold start de 30s |
| Reduzir retenção para 30d | -66% no S3 | Menos histórico |


---

## 8. Configuração

### 8.1 Variáveis de Ambiente (Produção)

| Variável | Valor | Descrição |
|----------|-------|-----------|
| `BRAND_WATCHDOG_STORAGE_DATABASE_URL` | `postgresql+asyncpg://watchdog:***@brand-watchdog-cluster.cluster-xxx.us-east-1.rds.amazonaws.com:5432/brand_watchdog` | Connection string do RDS |
| `BRAND_WATCHDOG_ALERT_SES_SENDER` | `suporteott6@gmail.com` | Email remetente (verificado no SES) |
| `BRAND_WATCHDOG_ALERT_RECIPIENTS` | `hudson.venturaramos@sky.com.br` | Destinatários dos alertas |
| `BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS` | `24` | Intervalo entre ciclos |
| `BRAND_WATCHDOG_ANALYZER_BEDROCK_REGION` | `us-east-1` | Região do Bedrock |
| `BRAND_WATCHDOG_STORAGE_SCREENSHOT_BASE_PATH` | `/app/data/screenshots` | Path no container |
| `AWS_DEFAULT_REGION` | `us-east-1` | Região padrão AWS |

### 8.2 Arquivo config.yaml

```yaml
crawler:
  viewport_width: 1280          # Largura do viewport Playwright
  page_timeout_seconds: 60      # Timeout por página
  network_idle_timeout_ms: 500  # Tempo para considerar network idle
  max_screenshot_height_px: 20000  # Limite de altura (trunca acima)

analyzer:
  bedrock_model_id: "anthropic.claude-sonnet-4-6"  # Modelo Claude
  bedrock_region: "us-east-1"
  confidence_threshold: 70      # Threshold para alertar (0-100)
  request_timeout_seconds: 60   # Timeout da chamada Bedrock
  max_retries: 3                # Retries com exponential backoff

alert:
  provider: "ses"               # "ses" ou "smtp"
  ses_region: "us-east-1"
  retry_attempts: 3
  retry_interval_seconds: 30

schedule:
  interval_hours: 24            # Frequência (1-720 horas)

storage:
  screenshot_retention_days: 90 # Retenção de screenshots
  detection_retention_days: 90  # Retenção de detecções
```

---

## 9. Operações

### 9.1 Deploy (Push de Nova Versão)

```powershell
# 1. Atualizar código
# 2. Recriar zip e enviar para S3
Remove-Item source.zip -Force
tar -acf source.zip -C . Dockerfile .dockerignore buildspec.yml config.yaml pyproject.toml brand_watchdog
aws s3 cp source.zip s3://brand-watchdog-build-761018874615/source.zip --region us-east-1

# 3. Disparar build no CodeBuild
aws codebuild start-build --project-name brand-watchdog-build --region us-east-1

# 4. Aguardar build (2-3 min)
# 5. Forçar redeploy
aws ecs update-service --cluster brand-watchdog-cluster --service brand-watchdog-service --force-new-deployment --region us-east-1
```

### 9.2 Monitoramento

```powershell
# Logs em tempo real
aws logs tail /ecs/brand-watchdog --follow --region us-east-1

# Status do serviço
aws ecs describe-services --cluster brand-watchdog-cluster --services brand-watchdog-service --region us-east-1

# Últimas detecções (query no banco via ECS run-task)
# Usar cli.py list-sites ou run-cycle
```

### 9.3 Administração via CLI

```powershell
# Template de override JSON para run-task
# Editar o campo "command" conforme necessidade

# Adicionar site
aws ecs run-task --cluster brand-watchdog-cluster --task-definition brand-watchdog --launch-type FARGATE --network-configuration "awsvpcConfiguration={subnets=[subnet-0b2eed8647415c4ea],securityGroups=[sg-04a0fe5a84802d79a],assignPublicIp=DISABLED}" --overrides file://infra/override-add-site.json --region us-east-1

# Listar sites
aws ecs run-task ... --overrides file://infra/override-list-sites.json ...

# Disparar ciclo manual
aws ecs run-task ... --overrides file://infra/override-run-cycle.json ...
```

### 9.4 Destruição Total (Encerrar Custos)

```powershell
.\infra\destroy.ps1
# Digitar "DESTRUIR" para confirmar
# Remove TUDO: VPC, RDS, S3, ECS, ECR, NAT, logs
# Custo após destruição: $0/mês
```


---

## 10. Container (Docker)

### 10.1 Especificação

| Propriedade | Valor |
|-------------|-------|
| Base image | `python:3.12-slim` |
| Browser | Chromium (via Playwright) |
| Tamanho da imagem | ~1.4 GB |
| CPU (Fargate) | 1 vCPU (1024 units) |
| Memória (Fargate) | 4 GB |
| Usuário | `appuser` (non-root) |
| Health check | `python -c "print('healthy')"` a cada 60s |
| Entry point | `python -m brand_watchdog.main` |
| Volumes | `/app/data/screenshots`, `/app/data/logos` |

### 10.2 Dependências Python

| Pacote | Versão | Propósito |
|--------|--------|-----------|
| sqlalchemy[asyncio] | ≥2.0.0 | ORM + async engine |
| asyncpg | ≥0.29.0 | Driver PostgreSQL async |
| aiosqlite | ≥0.19.0 | Driver SQLite async (dev) |
| playwright | ≥1.40.0 | Browser automation |
| boto3 | ≥1.34.0 | AWS SDK (Bedrock, SES, S3) |
| apscheduler | ≥3.10.0 | Agendamento de jobs |
| tenacity | ≥8.2.0 | Retry com backoff |
| pyyaml | ≥6.0.0 | Parsing de config YAML |
| aiosmtplib | ≥3.0.0 | SMTP async (fallback) |

---

## 11. Processo de Análise (IA)

### 11.1 Modelo Utilizado

| Propriedade | Valor |
|-------------|-------|
| Provedor | Anthropic (via AWS Bedrock) |
| Modelo | Claude Sonnet 4.6 |
| Model ID | `anthropic.claude-sonnet-4-6` |
| Região | us-east-1 |
| API Version | bedrock-2023-05-31 |
| Max tokens | 4096 |
| Timeout | 60 segundos |
| Retries | 3 (backoff: 2s, 4s, 8s) |

### 11.2 Prompt de Análise

O sistema envia ao modelo:
1. **Imagem**: Screenshot PNG full-page (base64)
2. **Prompt**: Lista de logos (por filename) e textos a detectar, com instruções para retornar JSON estruturado

### 11.3 Formato de Resposta Esperado

```json
{
  "detections": [
    {
      "match_type": "text",
      "confidence": 95,
      "bounding_box": {
        "x_percent": 2.0,
        "y_percent": 0.1,
        "width_percent": 8.0,
        "height_percent": 1.2
      },
      "description": "Logo/texto 'SKY' no canto superior esquerdo"
    }
  ]
}
```

### 11.4 Thresholds de Confiança

| Threshold | Ação |
|-----------|------|
| ≥ 60 | Detecção confirmada (persistida no banco) |
| ≥ 70 | Alerta enviado por email |
| < 60 | Descartada (não salva) |

---

## 12. Testes

### 12.1 Cobertura

| Tipo | Quantidade | Tempo |
|------|-----------|-------|
| Unit tests | 283 | ~20s |
| Property tests (Hypothesis) | 93 | ~40s |
| Integration tests | 41 | ~7s |
| **Total** | **417** | **~67s** |

### 12.2 Execução

```bash
# Todos os testes
python -m pytest tests/ -v

# Apenas property tests
python -m pytest tests/property/ -v

# Com cobertura
python -m pytest tests/ --cov=brand_watchdog --cov-report=html
```

---

## 13. Resultados Comprovados

### 13.1 Primeiro Ciclo Real (01/07/2026)

| Métrica | Valor |
|---------|-------|
| Target site | https://www.skymais.com.br/home |
| Brand text monitorado | "SKY" |
| Tempo total do ciclo | 86 segundos |
| Screenshot capturado | 9181px de altura, 3.4 MB |
| Detecções encontradas | **10** |
| Confiança média | **91.5%** |
| Emails enviados | 10 (1 por detecção) |
| Destinatário | hudson.venturaramos@sky.com.br |

### 13.2 Detecções Identificadas

| # | Confiança | Localização |
|---|-----------|-------------|
| 1 | 95% | Logo/texto SKY na navbar (topo esquerdo) |
| 2 | 97% | Título principal: "TV e streaming SKY+" |
| 3 | 95% | Subtítulo: "SKY+ é seu aplicativo..." |
| 4 | 90% | Seção "Conheça nossos planos" |
| 5 | 88% | Card "Plano Streaming" |
| 6 | 88% | Card "Plano Futebol" |
| 7 | 88% | Card "Plano Futebol+" |
| 8 | 92% | Seção esportes: "a SKY+ é o lugar" |
| 9 | 92% | "Futebol é na SKY+" |
| 10 | 90% | "Curta seus programas favoritos na SKY+" |

---

## 14. Roadmap Futuro

- [ ] API HTTP (FastAPI) para gerenciamento sem ECS run-task
- [ ] Dashboard web para visualização de detecções
- [ ] Suporte a S3 para screenshots (ao invés de filesystem local)
- [ ] Detecção de logos por imagem (upload de referência + comparação visual)
- [ ] Webhook como alternativa a email
- [ ] Multi-tenant (múltiplas contas de marca)
- [ ] Export de relatórios (PDF/CSV)
- [ ] VPC Endpoints para reduzir custo do NAT Gateway
