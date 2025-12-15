"""
Portfolio snapshot service for storing and retrieving account equity history.

Snapshots are stored in S3 with the following structure:
  s3://{bucket}/portfolio-snapshots/account-{account_number}/{date}.json

This allows accurate calculation of period-specific returns (monthly, YTD, etc.)
"""

import json
import logging
from datetime import datetime, date
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "portfolio-snapshots"


class PortfolioSnapshot:
    """Represents a portfolio snapshot at a specific point in time."""

    def __init__(self, account_number: str, timestamp: str, equity: float, cash: float):
        self.account_number = account_number
        self.timestamp = timestamp
        self.equity = equity
        self.cash = cash

    def to_dict(self) -> dict:
        """Convert snapshot to dictionary for JSON serialization."""
        return {
            "account_number": self.account_number,
            "timestamp": self.timestamp,
            "equity": self.equity,
            "cash": self.cash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PortfolioSnapshot":
        """Create snapshot from dictionary."""
        return cls(
            account_number=data["account_number"],
            timestamp=data["timestamp"],
            equity=float(data["equity"]),
            cash=float(data["cash"]),
        )


class SnapshotService:
    """Service for managing portfolio snapshots in S3."""

    def __init__(self, bucket: str, region: str = "us-east-1"):
        """
        Initialize the snapshot service.

        Args:
            bucket: S3 bucket name for storing snapshots
            region: AWS region
        """
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region)

    def save_snapshot(
        self, account_number: str, equity: float, cash: float, timestamp: Optional[datetime] = None
    ) -> bool:
        """
        Save a portfolio snapshot to S3.

        Args:
            account_number: Robinhood account number
            equity: Current account equity
            cash: Current cash balance
            timestamp: Snapshot timestamp (defaults to now)

        Returns:
            True if successful, False otherwise
        """
        if not timestamp:
            timestamp = datetime.now()

        try:
            snapshot = PortfolioSnapshot(
                account_number=account_number,
                timestamp=timestamp.isoformat(),
                equity=equity,
                cash=cash,
            )

            # Create S3 key based on date (YYYY-MM-DD)
            date_str = timestamp.strftime("%Y-%m-%d")
            key = f"{SNAPSHOT_PREFIX}/account-{account_number}/{date_str}.json"

            # Upload to S3
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=json.dumps(snapshot.to_dict()),
                ContentType="application/json",
            )

            logger.info(f"Saved snapshot for account {account_number} to s3://{self.bucket}/{key}")
            return True

        except ClientError as e:
            logger.error(f"Failed to save snapshot to S3: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error saving snapshot: {str(e)}")
            return False

    def get_snapshot(
        self, account_number: str, snapshot_date: date
    ) -> Optional[PortfolioSnapshot]:
        """
        Retrieve a snapshot for a specific date.

        Args:
            account_number: Robinhood account number
            snapshot_date: Date of the snapshot to retrieve

        Returns:
            PortfolioSnapshot if found, None otherwise
        """
        try:
            date_str = snapshot_date.strftime("%Y-%m-%d")
            key = f"{SNAPSHOT_PREFIX}/account-{account_number}/{date_str}.json"

            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            data = json.loads(response["Body"].read().decode("utf-8"))

            logger.info(f"Retrieved snapshot for account {account_number} on {date_str}")
            return PortfolioSnapshot.from_dict(data)

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.debug(f"No snapshot found for account {account_number} on {snapshot_date}")
            else:
                logger.error(f"Error retrieving snapshot: {str(e)}")
        except Exception as e:
            logger.error(f"Error parsing snapshot: {str(e)}")

        return None

    def get_latest_snapshot(self, account_number: str) -> Optional[PortfolioSnapshot]:
        """
        Retrieve the most recent snapshot for an account.

        Args:
            account_number: Robinhood account number

        Returns:
            Latest PortfolioSnapshot or None if no snapshots exist
        """
        try:
            # List all snapshots for this account (sorted by date)
            prefix = f"{SNAPSHOT_PREFIX}/account-{account_number}/"
            response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)

            if "Contents" not in response or not response["Contents"]:
                logger.debug(f"No snapshots found for account {account_number}")
                return None

            # Get the most recent file (last one when sorted)
            latest_key = sorted(response["Contents"], key=lambda x: x["Key"])[-1]["Key"]

            data = json.loads(
                self.s3.get_object(Bucket=self.bucket, Key=latest_key)["Body"].read().decode("utf-8")
            )

            logger.info(f"Retrieved latest snapshot for account {account_number}: {latest_key}")
            return PortfolioSnapshot.from_dict(data)

        except ClientError as e:
            logger.error(f"Error retrieving latest snapshot: {str(e)}")
        except Exception as e:
            logger.error(f"Error processing snapshot: {str(e)}")

        return None

    def calculate_period_change(
        self, account_number: str, start_date: date, end_date: Optional[date] = None
    ) -> Optional[dict]:
        """
        Calculate account change between two dates.

        Args:
            account_number: Robinhood account number
            start_date: Start date for the period
            end_date: End date (defaults to today)

        Returns:
            Dictionary with change_dollars and change_percent, or None if data unavailable
        """
        if not end_date:
            end_date = date.today()

        start_snapshot = self.get_snapshot(account_number, start_date)
        end_snapshot = self.get_snapshot(account_number, end_date)

        if not start_snapshot or not end_snapshot:
            logger.warning(
                f"Cannot calculate period change: "
                f"start={start_snapshot is not None}, end={end_snapshot is not None}"
            )
            return None

        change_dollars = end_snapshot.equity - start_snapshot.equity

        # Calculate percentage change
        if start_snapshot.equity > 0:
            change_percent = (change_dollars / start_snapshot.equity) * 100
        else:
            change_percent = 0.0

        return {
            "change_dollars": change_dollars,
            "change_percent": change_percent,
            "start_equity": start_snapshot.equity,
            "end_equity": end_snapshot.equity,
        }
