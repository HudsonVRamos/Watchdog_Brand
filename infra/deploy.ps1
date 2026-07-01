# ==============================================================
# Brand Watchdog - Script de Deploy Completo (PowerShell)
# Cria toda a infraestrutura e faz deploy da aplicação
# ==============================================================

$ErrorActionPreference = "Stop"

# --- Configuração ---
$ProjectName = "brand-watchdog"
$StackName = "brand-watchdog-stack"
$Region = "us-east-1"
$Environment = "prod"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Brand Watchdog - Deploy Script" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# Solicita parâmetros
$DBPassword = Read-Host "Senha do banco de dados (min 8 chars)" -AsSecureString
$DBPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($DBPassword))
$AlertSender = Read-Host "Email remetente SES (deve estar verificado)"
$AlertRecipients = Read-Host "Emails destinatarios (separados por virgula)"
$IntervalHours = Read-Host "Intervalo de monitoramento em horas [24]"
if ([string]::IsNullOrEmpty($IntervalHours)) { $IntervalHours = "24" }

Write-Host ""
Write-Host "Configuracao:" -ForegroundColor Yellow
Write-Host "  Regiao: $Region"
Write-Host "  Remetente: $AlertSender"
Write-Host "  Destinatarios: $AlertRecipients"
Write-Host "  Intervalo: ${IntervalHours}h"
Write-Host ""
$Confirm = Read-Host "Prosseguir? (y/n)"
if ($Confirm -ne "y") { Write-Host "Cancelado."; exit 0 }

# --- Etapa 1: CloudFormation ---
Write-Host ""
Write-Host "[1/4] Criando infraestrutura via CloudFormation..." -ForegroundColor Green

aws cloudformation deploy `
    --template-file infra/cloudformation.yml `
    --stack-name $StackName `
    --region $Region `
    --parameter-overrides `
        "ProjectName=$ProjectName" `
        "Environment=$Environment" `
        "DBMasterPassword=$DBPasswordPlain" `
        "AlertSender=$AlertSender" `
        "AlertRecipients=$AlertRecipients" `
        "MonitoringIntervalHours=$IntervalHours" `
        "ImageUri=public.ecr.aws/docker/library/python:3.12-slim" `
    --capabilities CAPABILITY_NAMED_IAM `
    --no-fail-on-empty-changeset

Write-Host "     Infraestrutura criada!" -ForegroundColor Green

# --- Etapa 2: Obter ECR URI ---
Write-Host ""
Write-Host "[2/4] Obtendo URI do repositorio ECR..." -ForegroundColor Green

$EcrUri = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query "Stacks[0].Outputs[?OutputKey=='ECRRepositoryUri'].OutputValue" `
    --output text

Write-Host "     ECR URI: $EcrUri"

# --- Etapa 3: Build e push ---
Write-Host ""
Write-Host "[3/4] Build e push da imagem Docker..." -ForegroundColor Green

$AccountId = aws sts get-caller-identity --query Account --output text
$LoginPassword = aws ecr get-login-password --region $Region
$LoginPassword | docker login --username AWS --password-stdin "$AccountId.dkr.ecr.$Region.amazonaws.com"

docker build -t "${ProjectName}:latest" .
docker tag "${ProjectName}:latest" "${EcrUri}:latest"
docker push "${EcrUri}:latest"

Write-Host "     Imagem enviada: ${EcrUri}:latest" -ForegroundColor Green

# --- Etapa 4: Atualizar ECS ---
Write-Host ""
Write-Host "[4/4] Atualizando ECS com a imagem real..." -ForegroundColor Green

aws cloudformation deploy `
    --template-file infra/cloudformation.yml `
    --stack-name $StackName `
    --region $Region `
    --parameter-overrides `
        "ProjectName=$ProjectName" `
        "Environment=$Environment" `
        "DBMasterPassword=$DBPasswordPlain" `
        "AlertSender=$AlertSender" `
        "AlertRecipients=$AlertRecipients" `
        "MonitoringIntervalHours=$IntervalHours" `
        "ImageUri=${EcrUri}:latest" `
    --capabilities CAPABILITY_NAMED_IAM `
    --no-fail-on-empty-changeset

aws ecs update-service `
    --cluster "$ProjectName-cluster" `
    --service "$ProjectName-service" `
    --force-new-deployment `
    --region $Region | Out-Null

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Deploy concluido com sucesso!" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Logs: aws logs tail /ecs/$ProjectName --follow --region $Region"
Write-Host "Destruir: .\infra\destroy.ps1"
Write-Host ""
