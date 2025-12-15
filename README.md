# Monthly Portfolio Newsletter Bot

A serverless application that automatically sends you a monthly email summary of your Robinhood portfolio, including retirement progress tracking.

<img width="356" height="598" alt="image" src="https://github.com/user-attachments/assets/138e7157-c088-42e0-a290-0ef90b0f45ad" />

<img width="336" height="584" alt="image" src="https://github.com/user-attachments/assets/d1083ba0-65ba-401a-80d9-1fa1b22c51fc" />



## Features

- **Portfolio Summary**: Total value, monthly and YTD performance
- **Top Holdings**: Your largest positions with gains/losses
- **Dividend Tracking**: Monthly and year-to-date dividends received
- **Retirement Progress**: Track progress toward your retirement goal with projections
- **Automated Delivery**: Runs on the 1st of each month via AWS Lambda

## Architecture

```
EventBridge (monthly) --> Lambda --> S3 (cached session)
                            |              |
                            v              v
                     AWS Secrets Manager   Robinhood API
                            |
                            v
                        AWS SES (email)
```

## Prerequisites

1. **Robinhood Account** with device approval enabled
2. **AWS Account** (free tier eligible)
3. **Python 3.12+**
4. **AWS CLI** and **SAM CLI** installed

## Quick Start

### 1. Clone and Setup

```powershell
cd C:\Users\Francis\PycharmProjects\MonthlyPortfolioBot

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements-dev.txt
```

### 2. Authenticate and Cache Session

Robinhood removed TOTP authentication in Dec 2024. This bot uses **session caching** - you log in once locally (with device approval via SMS/email), and the session is cached to S3 for Lambda to reuse.

```powershell
# Run local test to authenticate and cache session
python scripts/local_test.py --dry-run
```

1. Enter your Robinhood credentials when prompted
2. Robinhood will send an SMS/email code for device approval
3. Enter the code - your session will be cached to S3
4. Lambda can now reuse this cached session

**Note:** Sessions may expire periodically. If Lambda fails with auth errors, re-run the local test to refresh the cached session.

### 3. Local Testing

Create a `.env` file from the example:

```powershell
Copy-Item .env.example .env
# Edit .env with your credentials
```

Test locally (dry run - no email sent):

```powershell
python scripts/local_test.py --dry-run
```

### 4. AWS Setup

#### Step A: Create AWS Account (if needed)
1. Go to https://aws.amazon.com and click "Create an AWS Account"
2. Follow signup (requires credit card, but free tier covers this project)

#### Step B: Create IAM User and Get Access Keys
1. Sign into AWS Console and go to **IAM**: https://console.aws.amazon.com/iam/
2. Click **"Users"** → **"Create user"**
3. User name: `portfolio-bot-admin`, click **Next**
4. Select **"Attach policies directly"** and check these policies:
   - `AWSLambda_FullAccess`
   - `AmazonSESFullAccess`
   - `SecretsManagerReadWrite`
   - `AmazonEventBridgeSchedulerFullAccess`
   - `IAMFullAccess`
   - `AmazonS3FullAccess`
   - `CloudWatchLogsFullAccess`
   - `AWSCloudFormationFullAccess`
   - *(Or just use `AdministratorAccess` for simplicity)*
5. Click **Next** → **"Create user"**
6. Click on the user → **"Security credentials"** tab
7. Under **"Access keys"**, click **"Create access key"**
8. Select **"Command Line Interface (CLI)"**, check confirmation, click **Next** → **"Create access key"**
9. **SAVE BOTH KEYS NOW** (shown only once!):
   - Access key ID: `AKIA...`
   - Secret access key: `wJalr...` (click "Show")

#### Step C: Install and Configure AWS CLI
```powershell
# Download and install AWS CLI
msiexec.exe /i https://awscli.amazonaws.com/AWSCLIV2.msi

# Configure with your keys from Step B
aws configure
```
Enter when prompted:
- **AWS Access Key ID**: paste from Step B
- **AWS Secret Access Key**: paste from Step B
- **Default region**: `us-east-1`
- **Default output format**: `json`

Verify it works:
```powershell
aws sts get-caller-identity
```

#### Step D: Install SAM CLI
```powershell
winget install Amazon.SAM-CLI

# Verify
sam --version
```

#### Step E: Verify Your Email in SES
```powershell
aws ses verify-email-identity --email-address your-email@example.com
```
**Check your inbox and click the verification link from AWS.**

#### Step F: Create Secrets in AWS
Create a file `secrets.json` in the project folder (DO NOT commit this!):

```json
{
    "robinhood_username": "your_email@example.com",
    "robinhood_password": "your_password",
    "sender_email": "your-email@example.com",
    "recipient_email": "your-email@example.com",
    "retirement_target": "2000000",
    "retirement_age": "65",
    "current_age": "30",
    "monthly_contribution": "500"
}
```

Create the secret in AWS:
```powershell
aws secretsmanager create-secret --name monthly-portfolio-bot/credentials --secret-string file://secrets.json
```

To update later:
```powershell
aws secretsmanager update-secret --secret-id monthly-portfolio-bot/credentials --secret-string file://secrets.json
```

### 5. Deploy

```powershell
sam build
sam deploy --guided
```

During `sam deploy --guided`, use these settings:
- Stack name: `monthly-portfolio-newsletter`
- Region: `us-east-1`
- Parameter SecretName: `monthly-portfolio-bot/credentials`
- Parameter NotificationEmail: `your-email@example.com`
- Confirm changes: `Y`
- Allow SAM CLI IAM role creation: `Y`
- Disable rollback: `N`
- Save arguments: `Y`

### 6. Test the Lambda

```powershell
# Run the function manually
aws lambda invoke --function-name monthly-portfolio-newsletter output.json
type output.json

# View logs if it fails
aws logs tail /aws/lambda/monthly-portfolio-newsletter --since 1h
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_NAME` | Secrets Manager secret name | `monthly-portfolio-bot/credentials` |
| `AWS_REGION` | AWS region for SES | `us-east-1` |

### Secrets Manager Structure

| Key | Description |
|-----|-------------|
| `robinhood_username` | Robinhood account email |
| `robinhood_password` | Robinhood account password |
| `sender_email` | SES verified sender email |
| `recipient_email` | Your email to receive newsletters |
| `retirement_target` | Target retirement amount (e.g., 2000000) |
| `retirement_age` | Target retirement age |
| `current_age` | Your current age |
| `monthly_contribution` | Expected monthly contribution (for projections) |

### Schedule

Default: 1st of each month at 8:00 AM Eastern Time

To change, modify `ScheduleExpression` in `template.yaml`:

```yaml
# Every Sunday at 9 AM
cron(0 14 ? * SUN *)

# First Monday of each month at 8 AM
cron(0 13 ? * 2#1 *)
```

## Project Structure

```
MonthlyPortfolioBot/
├── src/
│   ├── __init__.py
│   ├── lambda_handler.py       # Main Lambda entry point
│   ├── robinhood_client.py     # Robinhood API wrapper
│   ├── portfolio_analyzer.py   # Portfolio metrics
│   ├── retirement_tracker.py   # Retirement calculations
│   ├── email_service.py        # AWS SES email
│   └── templates/
│       └── newsletter.html     # Email template
├── scripts/
│   ├── local_test.py           # Local testing
│   └── deploy.ps1              # Deployment script
├── tests/                      # Unit tests
├── template.yaml               # AWS SAM template
├── requirements.txt            # Production dependencies
├── requirements-dev.txt        # Development dependencies
├── .env.example                # Environment template
└── .gitignore
```

## Troubleshooting

### Login Failed

- Re-run `python scripts/local_test.py --dry-run` to refresh the cached session
- Check your Robinhood password hasn't changed
- Verify the S3 bucket for session caching exists and is accessible

### Email Not Sending

- Verify both sender and recipient emails in SES
- Check if SES is in sandbox mode (can only send to verified emails)
- Review CloudWatch logs for errors

### Lambda Timeout

- Increase timeout in `template.yaml` (default 60s)
- Robinhood API can be slow during market hours

## Security Notes

- Never commit `.env` or `secrets.json` files
- Credentials are stored in AWS Secrets Manager (encrypted at rest)
- Session cache is stored in a private S3 bucket with encryption
- IAM role follows least-privilege principle

## Cost

All AWS services used are free tier eligible:
- Lambda: 1M free requests/month
- Secrets Manager: First 30 days free, then ~$0.40/month
- SES: 62,000 emails/month free from Lambda
- S3: 5GB storage free (session cache is < 1KB)
- CloudWatch: 5GB logs free
- EventBridge: Free for scheduled rules

**Estimated monthly cost: $0 - $1**

## License

MIT
