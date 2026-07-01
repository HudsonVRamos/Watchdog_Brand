# Infraestrutura - Brand Watchdog

## Arquitetura

```
ECS Fargate (1 task, 2vCPU/4GB)
├── Playwright + Chromium (crawling)
├── Python 3.12 + APScheduler
└── Conecta a:
    ├── RDS Aurora PostgreSQL Serverless v2
    ├── S3 (screenshots)
    ├── AWS Bedrock (análise de marca)
    └── AWS SES (alertas por email)
```

## Pré-requisitos

1. **AWS CLI v2** configurado com credenciais (`aws configure`)
2. **Docker Desktop** instalado e rodando
3. **Conta AWS** com permissões para criar: VPC, ECS, RDS, S3, ECR, IAM
4. **SES** — email remetente verificado (Sandbox: verificar destinatários também)
5. **Bedrock** — acesso ao modelo Claude habilitado em us-east-1

## Deploy

### Windows (PowerShell)
```powershell
cd infra
.\deploy.ps1
```

### Linux/Mac (Bash)
```bash
chmod +x infra/deploy.sh
./infra/deploy.sh
```

O script vai:
1. Criar toda a infraestrutura via CloudFormation
2. Fazer build da imagem Docker
3. Push para ECR
4. Iniciar o container no ECS Fargate

## Destruição (encerrar custos)

### Windows (PowerShell)
```powershell
cd infra
.\destroy.ps1
```

### Linux/Mac (Bash)
```bash
chmod +x infra/destroy.sh
./infra/destroy.sh
```

⚠️ **IRREVERSÍVEL** — todos os dados serão perdidos.

## Custo estimado (~$125-140/mês)

| Recurso | Custo/mês |
|---------|-----------|
| ECS Fargate (2 vCPU, 4GB, 24/7) | ~$70 |
| RDS Aurora Serverless v2 (0.5-2 ACU) | ~$15-30 |
| NAT Gateway | ~$35 |
| S3 (screenshots, ~10GB) | ~$0.25 |
| CloudWatch Logs | ~$5 |
| Bedrock (Claude, por uso) | variável |

## Dev Local (docker-compose)

```bash
docker-compose up -d
```

Sobe a aplicação + PostgreSQL local. Configurar variáveis AWS para Bedrock/SES.

## Variáveis de Ambiente

| Variável | Descrição |
|----------|-----------|
| `BRAND_WATCHDOG_STORAGE_DATABASE_URL` | Connection string PostgreSQL |
| `BRAND_WATCHDOG_ALERT_SES_SENDER` | Email remetente (verificado no SES) |
| `BRAND_WATCHDOG_ALERT_RECIPIENTS` | Emails destinatários (vírgula) |
| `BRAND_WATCHDOG_SCHEDULE_INTERVAL_HOURS` | Intervalo entre ciclos (1-720) |
| `BRAND_WATCHDOG_ANALYZER_BEDROCK_REGION` | Região do Bedrock |

## Monitoramento

```bash
# Logs em tempo real
aws logs tail /ecs/brand-watchdog --follow --region us-east-1

# Status do serviço
aws ecs describe-services --cluster brand-watchdog-cluster --services brand-watchdog-service --region us-east-1

# Redeploy (após nova imagem)
aws ecs update-service --cluster brand-watchdog-cluster --service brand-watchdog-service --force-new-deployment --region us-east-1
```
