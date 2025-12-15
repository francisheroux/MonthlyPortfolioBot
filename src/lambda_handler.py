"""
AWS Lambda handler for the Monthly Portfolio Newsletter.

This is the main entry point that orchestrates:
1. Downloading cached Robinhood session from S3
2. Retrieving secrets from AWS Secrets Manager
3. Authenticating with Robinhood (using cached session)
4. Analyzing the portfolio
5. Calculating retirement progress
6. Sending the newsletter email
"""

import json
import logging
import os

import boto3

from .email_service import EmailService
from .portfolio_analyzer import PortfolioAnalyzer
from .retirement_tracker import RetirementConfig, RetirementTracker
from .robinhood_client import RobinhoodClient
from .snapshot_service import SnapshotService

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients outside handler for reuse across invocations
secrets_client = boto3.client("secretsmanager")
sns_client = boto3.client("sns")

# Configuration from environment variables
SECRET_NAME = os.environ.get("SECRET_NAME", "monthly-portfolio-bot/credentials")
SESSION_BUCKET = os.environ.get("SESSION_BUCKET", "")
ALERT_TOPIC_ARN = os.environ.get("ALERT_TOPIC_ARN", "")


def send_approval_alert():
    """Send SNS alert asking user to approve Robinhood login."""
    if not ALERT_TOPIC_ARN:
        logger.warning("ALERT_TOPIC_ARN not configured - cannot send alerts")
        return

    try:
        sns_client.publish(
            TopicArn=ALERT_TOPIC_ARN,
            Subject="ACTION REQUIRED: Approve Robinhood Login",
            Message=(
                "Your Robinhood session has expired.\n\n"
                "Please APPROVE the login request in your Robinhood app NOW.\n\n"
                "The system is waiting for your approval to send your monthly portfolio newsletter.\n"
                "You have about 2 minutes to approve."
            ),
        )
        logger.info("Sent approval alert via SNS")
    except Exception as e:
        logger.error(f"Failed to send SNS alert: {e}")

# Lambda can only write to /tmp, so we set HOME=/tmp
# This makes robin_stocks write to /tmp/.tokens/robinhood.pickle
os.environ["HOME"] = "/tmp"
LAMBDA_TOKENS_DIR = "/tmp/.tokens"
LAMBDA_PICKLE_PATH = f"{LAMBDA_TOKENS_DIR}/robinhood.pickle"


def get_secrets() -> dict:
    """
    Retrieve all secrets from AWS Secrets Manager.

    Returns:
        Dictionary containing all credential and configuration secrets
    """
    try:
        response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
        return json.loads(response["SecretString"])
    except Exception as e:
        logger.error(f"Failed to retrieve secrets: {str(e)}")
        raise


def lambda_handler(event, context):
    """
    Main Lambda entry point triggered by EventBridge.

    Args:
        event: EventBridge event (not used, but required by Lambda)
        context: Lambda context object

    Returns:
        Dictionary with statusCode and response body
    """
    logger.info("Starting Monthly Portfolio Newsletter Lambda")
    logger.info(f"Event: {json.dumps(event)}")

    rh_client = None

    try:
        # Step 1: Get credentials from Secrets Manager
        logger.info("Retrieving secrets from AWS Secrets Manager")
        secrets = get_secrets()

        # Step 2: Download cached session from S3
        if SESSION_BUCKET:
            # Create .tokens directory if it doesn't exist
            os.makedirs(LAMBDA_TOKENS_DIR, exist_ok=True)

            logger.info(f"Downloading cached session from S3 bucket: {SESSION_BUCKET}")
            logger.info(f"Session will be saved to: {LAMBDA_PICKLE_PATH}")
            session_downloaded = RobinhoodClient.download_session_from_s3(
                bucket=SESSION_BUCKET,
                local_path=LAMBDA_PICKLE_PATH
            )
            if session_downloaded:
                logger.info("Successfully downloaded cached session")
            else:
                logger.warning("No cached session found - login will require device approval (will likely fail)")
        else:
            logger.warning("SESSION_BUCKET not configured - cannot use cached sessions")

        # Step 3: Initialize and authenticate with Robinhood
        logger.info("Initializing Robinhood client")
        rh_client = RobinhoodClient(
            username=secrets["robinhood_username"],
            password=secrets["robinhood_password"],
            totp_secret=secrets.get("robinhood_totp_secret", ""),
        )

        # Try login with cached session
        if not rh_client.login():
            logger.warning("Cached session failed - attempting fresh login with device approval")

            # Send alert to user's phone/email
            send_approval_alert()

            # Try fresh login - this triggers device approval on user's phone
            # robin_stocks will wait for approval
            logger.info("Initiating fresh login - waiting for device approval...")
            if rh_client.login(store_session=True):
                logger.info("Fresh login successful! Uploading new session to S3...")
                # Save the new session to S3 for future use
                if SESSION_BUCKET:
                    RobinhoodClient.upload_session_to_s3(SESSION_BUCKET, LAMBDA_PICKLE_PATH)
            else:
                raise Exception(
                    "Failed to authenticate with Robinhood. "
                    "Device approval may have timed out or been rejected."
                )

        # Step 4: Analyze portfolio
        logger.info("Analyzing portfolio")

        # Initialize snapshot service if bucket is configured
        snapshot_service = None
        if SESSION_BUCKET:
            snapshot_service = SnapshotService(SESSION_BUCKET, os.environ.get("AWS_REGION", "us-east-1"))
            logger.info(f"Snapshot service initialized with bucket: {SESSION_BUCKET}")
        else:
            logger.warning("Snapshot service not available - SESSION_BUCKET not configured")

        analyzer = PortfolioAnalyzer(rh_client, snapshot_service)
        portfolio_report = analyzer.analyze()

        logger.info(f"Portfolio value: ${portfolio_report.total_value:,.2f}")

        # Step 5: Calculate retirement progress using combined portfolio value (Individual + IRA)
        logger.info("Calculating retirement progress")
        # Use birth_year if available (calculates age automatically), fall back to current_age
        birth_year = secrets.get("birth_year")
        current_age = int(secrets.get("current_age", 30)) if not birth_year else 0
        retirement_config = RetirementConfig(
            target_amount=float(secrets.get("retirement_target", 2000000)),
            target_age=int(secrets.get("retirement_age", 65)),
            current_age=current_age,
            monthly_contribution=float(secrets.get("monthly_contribution", 0)),
            birth_year=int(birth_year) if birth_year else None,
        )

        retirement_tracker = RetirementTracker(retirement_config)
        # Use combined value (Individual + IRA) for retirement tracking
        retirement_value = portfolio_report.combined_retirement_value or portfolio_report.total_value
        retirement_progress = retirement_tracker.calculate_progress(retirement_value)

        logger.info(f"Using combined retirement value: ${retirement_value:,.2f}")

        logger.info(
            f"Retirement progress: {retirement_progress.percent_complete}% - "
            f"{'On Track' if retirement_progress.on_track else 'Behind'}"
        )

        # Step 6: Send newsletter email
        logger.info("Sending newsletter email")
        email_service = EmailService(
            sender_email=secrets["sender_email"],
            recipient_email=secrets["recipient_email"],
            gmail_app_password=secrets.get("gmail_app_password"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
        )

        success = email_service.send_newsletter(portfolio_report, retirement_progress)

        if not success:
            raise Exception("Failed to send newsletter email")

        # Step 6: Cleanup
        logger.info("Newsletter sent successfully")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Newsletter sent successfully",
                    "portfolio_value": portfolio_report.total_value,
                    "retirement_progress": retirement_progress.percent_complete,
                    "on_track": retirement_progress.on_track,
                }
            ),
        }

    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}", exc_info=True)

        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "message": "Newsletter generation failed",
                    "error": str(e),
                }
            ),
        }

    finally:
        # Always attempt to logout from Robinhood
        if rh_client:
            try:
                rh_client.logout()
            except Exception as e:
                logger.warning(f"Error during Robinhood logout: {str(e)}")
