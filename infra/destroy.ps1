# ==============================================================
# Brand Watchdog - Script de Destruicao TOTAL (PowerShell)
# Remove TODA a infraestrutura e encerra custos imediatamente
#
# ATENCAO: Esta operacao e IRREVERSIVEL!
# Todos os dados (screenshots, deteccoes, banco) serao perdidos.
# ==============================================================

$ErrorActionPreference = "Stop"

$ProjectName = "brand-watchdog"
$StackName = "brand-watchdog-stack"
$Region = "us-east-1"

Write-Host "==============================================" -ForegroundColor Red
Write-Host "  DESTRUICAO TOTAL DA INFRAESTRUTURA" -ForegroundColor Red
Write-Host "==============================================" -ForegroundColor Red
Write-Host ""
Write-Host "Isso vai DELETAR permanentemente:" -ForegroundColor Yellow
Write-Host "  - Banco de dados (todos os dados de deteccao)"
Write-Host "  - S3 bucket (todos os screenshots)"
Write-Host "  - Container ECS (para a aplicacao)"
Write-Host "  - VPC, NAT Gateway, ECR"
Write-Host "  - Todos os logs no CloudWatch"
Write-Host ""
Write-Host "Custo apos destruicao: `$0/mes" -ForegroundColor Green
Write-Host ""

$Confirm = Read-Host "Tem certeza? Digite 'DESTRUIR' para confirmar"
if ($Confirm -ne "DESTRUIR") {
    Write-Host "Cancelado. Nenhum recurso foi alterado." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "[1/6] Parando o servico ECS..." -ForegroundColor Cyan
try {
    aws ecs update-service `
        --cluster "$ProjectName-cluster" `
        --service "$ProjectName-service" `
        --desired-count 0 `
        --region $Region | Out-Null
    Write-Host "     Servico parado."
} catch {
    Write-Host "     (servico nao encontrado, continuando...)" -ForegroundColor Gray
}

Start-Sleep -Seconds 10

Write-Host ""
Write-Host "[2/6] Esvaziando bucket S3..." -ForegroundColor Cyan
try {
    $AccountId = aws sts get-caller-identity --query Account --output text
    $BucketName = "$ProjectName-screenshots-$AccountId"
    aws s3 rm "s3://$BucketName" --recursive --region $Region 2>$null
    Write-Host "     Bucket esvaziado."
} catch {
    Write-Host "     (bucket nao encontrado ou ja vazio)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "[3/6] Deletando imagens do ECR..." -ForegroundColor Cyan
try {
    $Images = aws ecr list-images --repository-name $ProjectName --region $Region --query "imageIds[*]" --output json 2>$null
    if ($Images -and $Images -ne "[]") {
        aws ecr batch-delete-image `
            --repository-name $ProjectName `
            --image-ids $Images `
            --region $Region | Out-Null
    }
    Write-Host "     Imagens removidas."
} catch {
    Write-Host "     (repositorio nao encontrado ou sem imagens)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "[4/6] Deletando stack CloudFormation..." -ForegroundColor Cyan
Write-Host "     (isso remove VPC, RDS, ECS, NAT, Security Groups...)"
aws cloudformation delete-stack `
    --stack-name $StackName `
    --region $Region

Write-Host ""
Write-Host "[5/6] Aguardando exclusao da stack..." -ForegroundColor Cyan
Write-Host "     (pode levar 5-15 minutos para RDS e NAT Gateway)"
try {
    aws cloudformation wait stack-delete-complete `
        --stack-name $StackName `
        --region $Region
    Write-Host "     Stack deletada!" -ForegroundColor Green
} catch {
    Write-Host "     Timeout no wait. Verifique o console AWS." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[6/6] Deletando CloudWatch Log Group..." -ForegroundColor Cyan
try {
    aws logs delete-log-group `
        --log-group-name "/ecs/$ProjectName" `
        --region $Region 2>$null
    Write-Host "     Log group removido."
} catch {
    Write-Host "     (log group nao encontrado)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Infraestrutura destruida com sucesso!" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Todos os recursos foram removidos."
Write-Host "Custo recorrente: `$0/mes" -ForegroundColor Green
Write-Host ""
Write-Host "Nota: A cobranca do mes atual (pro-rata) ainda"
Write-Host "aparecera na proxima fatura AWS."
Write-Host ""
