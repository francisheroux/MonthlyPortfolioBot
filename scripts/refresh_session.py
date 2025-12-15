"""
Refresh Robinhood Session and Upload to S3.

Run this script locally when your Robinhood session expires.
It will:
1. Login to Robinhood (approve on your phone when prompted)
2. Upload the session pickle to S3 for Lambda to use

Usage:
    python scripts/refresh_session.py

Requires:
    - AWS CLI configured with credentials
    - .env file with Robinhood credentials
    - S3 bucket already created (from SAM deployment)
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(project_root / ".env")

import boto3
from botocore.exceptions import ClientError

from src.robinhood_client import RobinhoodClient, PICKLE_FILENAME


def get_s3_bucket_name() -> str:
    """Get the S3 bucket name from environment or AWS."""
    # First check environment variable
    bucket = os.getenv("SESSION_BUCKET")
    if bucket:
        return bucket

    # Try to get from CloudFormation stack outputs
    try:
        cf = boto3.client("cloudformation")
        response = cf.describe_stacks(StackName="monthly-portfolio-newsletter")
        outputs = response["Stacks"][0].get("Outputs", [])
        for output in outputs:
            if output["OutputKey"] == "SessionBucketName":
                return output["OutputValue"]
    except Exception:
        pass

    # Fallback to default naming convention
    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    return f"portfolio-bot-sessions-{account_id}"


def main():
    print("=" * 60)
    print("Robinhood Session Refresh Tool")
    print("=" * 60)

    # Validate environment
    username = os.getenv("ROBINHOOD_USERNAME")
    password = os.getenv("ROBINHOOD_PASSWORD")

    if not username or not password:
        print("\nERROR: Missing ROBINHOOD_USERNAME or ROBINHOOD_PASSWORD in .env file")
        sys.exit(1)

    # Get S3 bucket
    bucket = get_s3_bucket_name()
    print(f"\nS3 Bucket: {bucket}")

    # Verify bucket exists
    try:
        s3 = boto3.client("s3")
        s3.head_bucket(Bucket=bucket)
        print(f"Bucket verified: {bucket}")
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "404":
            print(f"\nERROR: Bucket {bucket} does not exist.")
            print("Run 'sam deploy' first to create the infrastructure.")
            sys.exit(1)
        elif error_code == "403":
            print(f"\nERROR: Access denied to bucket {bucket}.")
            print("Check your AWS credentials.")
            sys.exit(1)
        else:
            raise

    # Initialize Robinhood client
    print("\n" + "-" * 60)
    print("Logging into Robinhood...")
    print(">>> APPROVE THE LOGIN IN YOUR ROBINHOOD APP <<<")
    print("-" * 60 + "\n")

    # robin_stocks stores session at ~/.tokens/robinhood.pickle
    pickle_path = RobinhoodClient.get_pickle_path()
    print(f"Session will be saved to: {pickle_path}")

    client = RobinhoodClient(
        username=username,
        password=password,
    )

    if not client.login(store_session=True):
        print("\nERROR: Failed to login to Robinhood")
        print("Make sure you approved the login in your Robinhood app.")
        sys.exit(1)

    print("\nLogin successful!")

    # Verify pickle file was created
    if not os.path.exists(pickle_path):
        print(f"\nERROR: Session file not created at {pickle_path}")
        print("Checking .tokens directory...")
        tokens_dir = os.path.dirname(pickle_path)
        if os.path.exists(tokens_dir):
            print(f"Contents of {tokens_dir}:")
            for f in os.listdir(tokens_dir):
                print(f"  - {f}")
        sys.exit(1)

    print(f"Session saved to: {pickle_path}")

    # Upload to S3
    print(f"\nUploading session to S3...")
    if RobinhoodClient.upload_session_to_s3(bucket, pickle_path):
        print(f"Successfully uploaded to s3://{bucket}/{PICKLE_FILENAME}")
    else:
        print("\nERROR: Failed to upload session to S3")
        sys.exit(1)

    # Logout
    client.logout()

    print("\n" + "=" * 60)
    print("SESSION REFRESH COMPLETE!")
    print("=" * 60)
    print("\nYour Lambda function should now be able to authenticate.")
    print("Test it with:")
    print("  aws lambda invoke --function-name monthly-portfolio-newsletter output.json")
    print("  type output.json")


if __name__ == "__main__":
    main()
