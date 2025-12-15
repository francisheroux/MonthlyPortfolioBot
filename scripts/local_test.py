"""
Local testing script for the Monthly Portfolio Newsletter.

Usage:
    python scripts/local_test.py              # Full run with email
    python scripts/local_test.py --dry-run    # Skip email sending
    python scripts/local_test.py --test-email # Send test email only

Requires a .env file in the project root with credentials.
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(project_root / ".env")

from src.email_service import EmailService
from src.portfolio_analyzer import PortfolioAnalyzer
from src.retirement_tracker import RetirementConfig, RetirementTracker
from src.robinhood_client import RobinhoodClient


def print_separator():
    print("=" * 60)


def print_account_summary(account, account_label):
    """Print summary for a single account."""
    print(f"\n{account_label}:")
    print(f"  Value: ${account.total_value:,.2f}")
    print(f"  Cash: ${account.cash_balance:,.2f}")
    print(f"  Monthly: {account.monthly_change_percent:+.2f}%")
    print(f"  YTD: {account.ytd_change_percent:+.2f}%")
    print(f"  Holdings: {account.total_holdings_count}")

    if account.top_holdings:
        print(f"  Top Holdings:")
        for i, h in enumerate(account.top_holdings[:3], 1):
            print(f"    {i}. {h.symbol:6} ${h.total_value:>10,.2f}")


def print_portfolio_report(report):
    """Print portfolio report to console."""
    print_separator()
    print(f"PORTFOLIO REPORT - {report.report_date}")
    print_separator()

    # Individual Account
    print(f"\nINDIVIDUAL ACCOUNT")
    print("-" * 40)
    print(f"Total Value: ${report.total_value:,.2f}")
    print(f"Cash Balance: ${report.cash_balance:,.2f}")
    print(f"Monthly Change: ${report.monthly_change_dollars:+,.2f} ({report.monthly_change_percent:+.2f}%)")
    print(f"YTD Change: ${report.ytd_change_dollars:+,.2f} ({report.ytd_change_percent:+.2f}%)")

    print(f"\nTop {len(report.top_holdings)} Holdings (Individual):")
    print("-" * 50)
    for i, h in enumerate(report.top_holdings, 1):
        print(f"  {i}. {h.symbol:6} {h.name[:25]:25} ${h.total_value:>10,.2f} ({h.percent_change:+.2f}%)")

    # Roth IRA Account
    if report.ira_account:
        print(f"\nROTH IRA ACCOUNT")
        print("-" * 40)
        print(f"Total Value: ${report.ira_account.total_value:,.2f}")
        print(f"Cash Balance: ${report.ira_account.cash_balance:,.2f}")
        print(f"Monthly Change: ${report.ira_account.monthly_change_dollars:+,.2f} ({report.ira_account.monthly_change_percent:+.2f}%)")
        print(f"YTD Change: ${report.ira_account.ytd_change_dollars:+,.2f} ({report.ira_account.ytd_change_percent:+.2f}%)")

        if report.ira_account.top_holdings:
            print(f"\nTop {len(report.ira_account.top_holdings)} Holdings (Roth IRA):")
            print("-" * 50)
            for i, h in enumerate(report.ira_account.top_holdings, 1):
                print(f"  {i}. {h.symbol:6} {h.name[:25]:25} ${h.total_value:>10,.2f} ({h.percent_change:+.2f}%)")
        else:
            print(f"\n  (Holdings details not available for retirement accounts)")
    else:
        print(f"\nROTH IRA ACCOUNT: Not found or not linked")

    # Combined Summary
    print(f"\nCOMBINED PORTFOLIO SUMMARY")
    print("-" * 40)
    print(f"Combined Retirement Value: ${report.combined_retirement_value:,.2f}")
    individual_value = report.total_value
    ira_value = report.ira_account.total_value if report.ira_account else 0
    print(f"  Individual: ${individual_value:,.2f}")
    print(f"  Roth IRA: ${ira_value:,.2f}")

    print(f"\nDividends:")
    print(f"  This Month: ${report.monthly_dividends:,.2f}")
    print(f"  Year to Date: ${report.ytd_dividends:,.2f}")

    print(f"\nTotal Holdings: {report.total_holdings_count}")


def print_retirement_progress(progress):
    """Print retirement progress to console."""
    print_separator()
    print("RETIREMENT PROGRESS")
    print_separator()

    # Visual progress bar
    bar_width = 40
    filled = int(bar_width * min(progress.percent_complete, 100) / 100)
    bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
    print(f"\n{bar} {progress.percent_complete:.1f}%")

    print(f"\nCurrent Value: ${progress.current_value:,.2f}")
    print(f"Target Amount: ${progress.target_amount:,.2f}")
    print(f"Years Remaining: {progress.years_remaining}")

    status = "ON TRACK" if progress.on_track else "BEHIND TARGET"
    print(f"\nStatus: {status}")

    print(f"Projected Value at Retirement: ${progress.projected_value:,.2f}")

    if progress.on_track:
        print(f"Projected Surplus: ${progress.surplus_or_deficit:,.2f}")
    else:
        print(f"Projected Deficit: ${abs(progress.surplus_or_deficit):,.2f}")
        print(f"Monthly Savings Needed: ${progress.monthly_needed:,.2f}")


def validate_env_vars():
    """Check that required environment variables are set."""
    required = [
        "ROBINHOOD_USERNAME",
        "ROBINHOOD_PASSWORD",
    ]

    missing = [var for var in required if not os.getenv(var)]

    if missing:
        print("ERROR: Missing required environment variables:")
        for var in missing:
            print(f"  - {var}")
        print("\nCreate a .env file based on .env.example and fill in your credentials.")
        sys.exit(1)

    # TOTP secret is optional for local testing (will prompt for MFA)
    if not os.getenv("ROBINHOOD_TOTP_SECRET"):
        print("NOTE: ROBINHOOD_TOTP_SECRET not set - you'll be prompted for MFA code")
        print("      (For automated Lambda deployment, you'll need the TOTP secret)\n")


def main():
    parser = argparse.ArgumentParser(description="Test the Monthly Portfolio Newsletter locally")
    parser.add_argument("--dry-run", action="store_true", help="Skip sending email")
    parser.add_argument("--test-email", action="store_true", help="Send test email only")
    args = parser.parse_args()

    validate_env_vars()

    # Handle test email mode
    if args.test_email:
        sender = os.getenv("SENDER_EMAIL")
        recipient = os.getenv("RECIPIENT_EMAIL")

        if not sender or not recipient:
            print("ERROR: SENDER_EMAIL and RECIPIENT_EMAIL must be set for email testing")
            sys.exit(1)

        print(f"Sending test email from {sender} to {recipient}...")
        email_service = EmailService(
            sender_email=sender,
            recipient_email=recipient,
            region=os.getenv("AWS_REGION", "us-east-1"),
        )

        if email_service.send_test_email():
            print("Test email sent successfully!")
        else:
            print("Failed to send test email. Check your SES configuration.")
            sys.exit(1)
        return

    # Full portfolio analysis run
    print("\nMonthly Portfolio Newsletter - Local Test")
    print_separator()

    # Enable debug logging to see what robin_stocks is doing
    import logging
    logging.basicConfig(level=logging.INFO, format='%(name)s - %(message)s')

    # Initialize Robinhood client
    print("\nConnecting to Robinhood...")
    print("(Approve the login in your Robinhood app when prompted)")
    print()

    totp_secret = os.getenv("ROBINHOOD_TOTP_SECRET")
    client = RobinhoodClient(
        username=os.getenv("ROBINHOOD_USERNAME"),
        password=os.getenv("ROBINHOOD_PASSWORD"),
        totp_secret=totp_secret if totp_secret else None,
    )

    if not client.login():
        print("\nERROR: Failed to login to Robinhood")
        print("If you approved in the app, try running the script again.")
        print("The session may now be cached.")
        sys.exit(1)

    print("Successfully connected!")

    try:
        # Analyze portfolio
        print("\nAnalyzing portfolio...")
        analyzer = PortfolioAnalyzer(client)
        report = analyzer.analyze()
        print_portfolio_report(report)

        # Calculate retirement progress using combined value (Individual + IRA)
        # Use BIRTH_YEAR if available (calculates age automatically), fall back to CURRENT_AGE
        birth_year = os.getenv("BIRTH_YEAR")
        current_age = int(os.getenv("CURRENT_AGE", 30)) if not birth_year else 0
        config = RetirementConfig(
            target_amount=float(os.getenv("RETIREMENT_TARGET", 2000000)),
            target_age=int(os.getenv("RETIREMENT_AGE", 65)),
            current_age=current_age,
            monthly_contribution=float(os.getenv("MONTHLY_CONTRIBUTION", 0)),
            birth_year=int(birth_year) if birth_year else None,
        )

        tracker = RetirementTracker(config)
        # Use combined retirement value (Individual + IRA)
        retirement_value = report.combined_retirement_value or report.total_value
        progress = tracker.calculate_progress(retirement_value)
        print(f"\n(Using combined value of ${retirement_value:,.2f} for retirement tracking)")
        print_retirement_progress(progress)

        # Send email (unless dry run)
        if args.dry_run:
            print_separator()
            print("\n[DRY RUN] Email not sent")
        else:
            sender = os.getenv("SENDER_EMAIL")
            recipient = os.getenv("RECIPIENT_EMAIL")

            if sender and recipient:
                print_separator()
                print(f"\nSending newsletter to {recipient}...")

                email_service = EmailService(
                    sender_email=sender,
                    recipient_email=recipient,
                    region=os.getenv("AWS_REGION", "us-east-1"),
                )

                if email_service.send_newsletter(report, progress):
                    print("Newsletter sent successfully!")
                else:
                    print("Failed to send newsletter. Check your SES configuration.")
            else:
                print("\nSkipping email: SENDER_EMAIL or RECIPIENT_EMAIL not configured")

    finally:
        print("\nLogging out from Robinhood...")
        client.logout()

    print_separator()
    print("Done!")


if __name__ == "__main__":
    main()
