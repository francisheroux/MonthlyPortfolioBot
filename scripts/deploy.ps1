# Deploy Monthly Portfolio Newsletter to AWS
# Usage: .\scripts\deploy.ps1

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Monthly Portfolio Newsletter Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check prerequisites
Write-Host "`nChecking prerequisites..." -ForegroundColor Yellow

# Check AWS CLI
try {
    $awsVersion = aws --version
    Write-Host "  AWS CLI: $awsVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: AWS CLI not found. Install from https://aws.amazon.com/cli/" -ForegroundColor Red
    exit 1
}

# Check SAM CLI
try {
    $samVersion = sam --version
    Write-Host "  SAM CLI: $samVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: SAM CLI not found. Install from https://aws.amazon.com/serverless/sam/" -ForegroundColor Red
    exit 1
}

# Check AWS credentials
try {
    $identity = aws sts get-caller-identity --output json | ConvertFrom-Json
    Write-Host "  AWS Account: $($identity.Account)" -ForegroundColor Green
    Write-Host "  AWS User: $($identity.Arn)" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: AWS credentials not configured. Run 'aws configure'" -ForegroundColor Red
    exit 1
}

# Build the application
Write-Host "`nBuilding application..." -ForegroundColor Yellow
sam build

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    exit 1
}

Write-Host "Build successful!" -ForegroundColor Green

# Deploy the application
Write-Host "`nDeploying to AWS..." -ForegroundColor Yellow
Write-Host "This will launch an interactive deployment wizard." -ForegroundColor Cyan
Write-Host "For first-time deployment, use these recommended settings:" -ForegroundColor Cyan
Write-Host "  - Stack name: monthly-portfolio-newsletter" -ForegroundColor White
Write-Host "  - Region: us-east-1 (or your preferred region)" -ForegroundColor White
Write-Host "  - Allow SAM CLI IAM role creation: Y" -ForegroundColor White
Write-Host "  - Disable rollback: N" -ForegroundColor White
Write-Host "  - Save arguments to configuration file: Y" -ForegroundColor White

sam deploy --guided

if ($LASTEXITCODE -ne 0) {
    Write-Host "Deployment failed!" -ForegroundColor Red
    exit 1
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

Write-Host "`nNext steps:" -ForegroundColor Yellow
Write-Host "1. Create your secret in AWS Secrets Manager:" -ForegroundColor White
Write-Host "   aws secretsmanager create-secret --name monthly-portfolio-bot/credentials --secret-string file://secrets.json" -ForegroundColor Cyan
Write-Host ""
Write-Host "2. Verify your SES email addresses:" -ForegroundColor White
Write-Host "   aws ses verify-email-identity --email-address your-sender@email.com" -ForegroundColor Cyan
Write-Host "   aws ses verify-email-identity --email-address your-recipient@email.com" -ForegroundColor Cyan
Write-Host ""
Write-Host "3. Test the Lambda function:" -ForegroundColor White
Write-Host "   aws lambda invoke --function-name monthly-portfolio-newsletter output.json" -ForegroundColor Cyan
Write-Host ""
