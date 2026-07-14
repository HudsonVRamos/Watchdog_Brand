# Infrastructure - Configurações de Referência

Esta pasta contém os arquivos de configuração de referência da infraestrutura do Worker ECS do Brand Watchdog.

> **Nota:** A infraestrutura real é definida no CloudFormation em `infra/worker-infrastructure.yml`. Os arquivos JSON aqui servem como **documentação e referência rápida** para cada componente.

## Arquivos

| Arquivo | Descrição |
|---------|-----------|
| `ecs_task_definition.json` | Task Definition do Worker ECS (Fargate, 1 vCPU, 2GB RAM) |
| `auto_scaling.json` | Auto Scaling: 1-10 tasks, target tracking 5 msg/task |
| `sqs_config.json` | Fila SQS + DLQ: visibility 120s, maxReceiveCount=3 |
| `s3_lifecycle.json` | Lifecycle rule: expirar screenshots/ após 90 dias |

## CloudFormation Templates

| Template | Localização | Descrição |
|----------|-------------|-----------|
| Principal | `infra/cloudformation.yml` | VPC, RDS, S3, ECS Cluster, Coordinator |
| Worker | `infra/worker-infrastructure.yml` | SQS, DLQ, Worker Task Definition, Auto Scaling |

## Arquitetura do Worker

```
SQS (brand-watchdog-tasks)
    │
    ▼
ECS Fargate Worker (1-10 tasks)
    ├── Chromium (captura screenshot)
    ├── S3 (upload screenshot)
    ├── Bedrock (análise compliance)
    ├── RDS (persistência resultados)
    └── EventBridge (publicar evento)
    │
    ▼ (após 3 falhas)
DLQ (brand-watchdog-dlq)
```

## Parâmetros Chave

| Parâmetro | Valor | Justificativa |
|-----------|-------|---------------|
| CPU | 1024 (1 vCPU) | Suficiente para Chromium + processamento |
| Memória | 2048 MB (2 GB) | Chromium requer ~1.4 GB |
| Visibility Timeout | 120s | Compatível com timeout de processamento |
| Max Receive Count | 3 | Mensagens falhadas vão para DLQ |
| Scale Target | 5 msg/task | Equilíbrio entre latência e custo |
| Scale-in Cooldown | 120s | Evita oscilação excessiva |
| Scale-out Cooldown | 60s | Resposta rápida a picos de carga |
| Screenshot Retention | 90 dias | Período razoável para auditoria |
