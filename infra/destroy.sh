#!/bin/bash
# ==============================================================
# Brand Watchdog - Script de Destruição TOTAL
# Remove TODA a infraestrutura e encerra custos imediatamente
#
# ATENÇÃO: Esta operação é IRREVERSÍVEL!
# Todos os dados (screenshots, detecções, banco) serão perdidos.
# ==============================================================

set -euo pipefail

PROJECT_NAME="brand-watchdog"
STACK_NAME="brand-watchdog-stack"
REGION="us-east-1"

echo "=============================================="
echo "  ⚠️  DESTRUIÇÃO TOTAL DA INFRAESTRUTURA  ⚠️"
echo "=============================================="
echo ""
echo "Isso vai DELETAR permanentemente:"
echo "  - Banco de dados (todos os dados de detecção)"
echo "  - S3 bucket (todos os screenshots)"
echo "  - Container ECS (para a aplicação)"
echo "  - VPC, NAT Gateway, ECR"
echo "  - Todos os logs no CloudWatch"
echo ""
echo "Custo após destruição: $0/mês"
echo ""
read -p "Tem certeza? Digite 'DESTRUIR' para confirmar: " CONFIRM

if [[ "$CONFIRM" != "DESTRUIR" ]]; then
    echo "Cancelado. Nenhum recurso foi alterado."
    exit 0
fi

echo ""
echo "[1/6] Parando o serviço ECS..."
aws ecs update-service \
    --cluster "${PROJECT_NAME}-cluster" \
    --service "${PROJECT_NAME}-service" \
    --desired-count 0 \
    --region "${REGION}" 2>/dev/null || echo "     (serviço não encontrado, continuando...)"

# Aguarda tasks pararem
echo "     Aguardando tasks pararem..."
sleep 10

echo ""
echo "[2/6] Esvaziando bucket S3..."
BUCKET_NAME="${PROJECT_NAME}-screenshots-$(aws sts get-caller-identity --query Account --output text)"
aws s3 rm "s3://${BUCKET_NAME}" --recursive --region "${REGION}" 2>/dev/null || \
    echo "     (bucket não encontrado ou já vazio)"

echo ""
echo "[3/6] Deletando imagens do ECR..."
aws ecr batch-delete-image \
    --repository-name "${PROJECT_NAME}" \
    --image-ids "$(aws ecr list-images --repository-name "${PROJECT_NAME}" --region "${REGION}" --query 'imageIds[*]' --output json 2>/dev/null || echo '[]')" \
    --region "${REGION}" 2>/dev/null || \
    echo "     (repositório não encontrado ou sem imagens)"

echo ""
echo "[4/6] Deletando stack CloudFormation..."
echo "     (isso remove VPC, RDS, ECS, NAT, Security Groups...)"
aws cloudformation delete-stack \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}"

echo ""
echo "[5/6] Aguardando exclusão da stack..."
echo "     (pode levar 5-15 minutos para RDS e NAT Gateway)"
aws cloudformation wait stack-delete-complete \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" 2>/dev/null || \
    echo "     ⚠️  Timeout no wait. Verifique o console AWS."

echo ""
echo "[6/6] Deletando CloudWatch Log Group..."
aws logs delete-log-group \
    --log-group-name "/ecs/${PROJECT_NAME}" \
    --region "${REGION}" 2>/dev/null || \
    echo "     (log group não encontrado)"

echo ""
echo "=============================================="
echo "  ✅ Infraestrutura destruída com sucesso!"
echo "=============================================="
echo ""
echo "Todos os recursos foram removidos."
echo "Custo recorrente: $0/mês"
echo ""
echo "Nota: A cobrança do mês atual (pro-rata) ainda"
echo "aparecerá na próxima fatura AWS."
echo ""
