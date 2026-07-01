#!/bin/bash
# ==============================================================
# Brand Watchdog - Script de Deploy Completo
# Cria toda a infraestrutura e faz deploy da aplicação
# ==============================================================

set -euo pipefail

# --- Configuração ---
PROJECT_NAME="brand-watchdog"
STACK_NAME="brand-watchdog-stack"
REGION="us-east-1"
ENVIRONMENT="prod"

# Solicita parâmetros obrigatórios
echo "=============================================="
echo "  Brand Watchdog - Deploy Script"
echo "=============================================="
echo ""

read -sp "Senha do banco de dados (min 8 chars): " DB_PASSWORD
echo ""
read -p "Email remetente SES (deve estar verificado): " ALERT_SENDER
read -p "Emails destinatários (separados por vírgula): " ALERT_RECIPIENTS
read -p "Intervalo de monitoramento em horas [24]: " INTERVAL_HOURS
INTERVAL_HOURS=${INTERVAL_HOURS:-24}

echo ""
echo "Configuração:"
echo "  Região: ${REGION}"
echo "  Remetente: ${ALERT_SENDER}"
echo "  Destinatários: ${ALERT_RECIPIENTS}"
echo "  Intervalo: ${INTERVAL_HOURS}h"
echo ""
read -p "Prosseguir? (y/n): " CONFIRM
[[ "$CONFIRM" != "y" ]] && echo "Cancelado." && exit 0

# --- Etapa 1: Criar stack CloudFormation (sem ImageUri inicialmente) ---
echo ""
echo "[1/4] Criando infraestrutura via CloudFormation..."
echo "     (VPC, RDS, S3, ECR, ECS...)"

# Primeiro deploy: usa imagem placeholder para criar o ECR
aws cloudformation deploy \
    --template-file infra/cloudformation.yml \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --parameter-overrides \
        ProjectName="${PROJECT_NAME}" \
        Environment="${ENVIRONMENT}" \
        DBMasterPassword="${DB_PASSWORD}" \
        AlertSender="${ALERT_SENDER}" \
        AlertRecipients="${ALERT_RECIPIENTS}" \
        MonitoringIntervalHours="${INTERVAL_HOURS}" \
        ImageUri="public.ecr.aws/docker/library/python:3.12-slim" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset

echo "     Infraestrutura criada!"

# --- Etapa 2: Obter URI do ECR ---
echo ""
echo "[2/4] Obtendo URI do repositório ECR..."

ECR_URI=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='ECRRepositoryUri'].OutputValue" \
    --output text)

echo "     ECR URI: ${ECR_URI}"

# --- Etapa 3: Build e push da imagem Docker ---
echo ""
echo "[3/4] Fazendo build e push da imagem Docker..."

# Login no ECR
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws ecr get-login-password --region "${REGION}" | \
    docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Build
docker build -t "${PROJECT_NAME}:latest" .

# Tag e push
docker tag "${PROJECT_NAME}:latest" "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

echo "     Imagem enviada: ${ECR_URI}:latest"

# --- Etapa 4: Atualizar stack com imagem real ---
echo ""
echo "[4/4] Atualizando ECS com a imagem real..."

aws cloudformation deploy \
    --template-file infra/cloudformation.yml \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --parameter-overrides \
        ProjectName="${PROJECT_NAME}" \
        Environment="${ENVIRONMENT}" \
        DBMasterPassword="${DB_PASSWORD}" \
        AlertSender="${ALERT_SENDER}" \
        AlertRecipients="${ALERT_RECIPIENTS}" \
        MonitoringIntervalHours="${INTERVAL_HOURS}" \
        ImageUri="${ECR_URI}:latest" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset

# Força novo deploy no ECS
aws ecs update-service \
    --cluster "${PROJECT_NAME}-cluster" \
    --service "${PROJECT_NAME}-service" \
    --force-new-deployment \
    --region "${REGION}" > /dev/null

echo ""
echo "=============================================="
echo "  Deploy concluído com sucesso!"
echo "=============================================="
echo ""
echo "Recursos criados:"
echo "  - VPC com subnets públicas/privadas"
echo "  - NAT Gateway para acesso à internet"
echo "  - RDS Aurora PostgreSQL Serverless v2"
echo "  - S3 bucket para screenshots"
echo "  - ECR repository para imagens Docker"
echo "  - ECS Fargate Service (1 task)"
echo "  - CloudWatch Logs"
echo ""
echo "Logs: aws logs tail /ecs/${PROJECT_NAME} --follow --region ${REGION}"
echo ""
echo "Para destruir tudo: ./infra/destroy.sh"
echo ""
