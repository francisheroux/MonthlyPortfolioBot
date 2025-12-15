"""
Portfolio analyzer for generating metrics and reports.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from dateutil.relativedelta import relativedelta

from .robinhood_client import RobinhoodClient
from .snapshot_service import SnapshotService

logger = logging.getLogger(__name__)


@dataclass
class Holding:
    """Represents a single stock holding."""

    symbol: str
    name: str
    quantity: float
    current_price: float
    average_cost: float
    total_value: float
    percent_change: float
    portfolio_percent: float


@dataclass
class AccountReport:
    """Report for a single account (Individual or IRA)."""

    account_type: str  # "individual" or "roth_ira"
    account_number: str
    total_value: float
    cash_balance: float
    monthly_change_dollars: float
    monthly_change_percent: float
    ytd_change_dollars: float
    ytd_change_percent: float
    top_holdings: list[Holding]
    total_holdings_count: int


@dataclass
class PortfolioReport:
    """Complete portfolio report with all metrics."""

    total_value: float
    cash_balance: float
    monthly_change_dollars: float
    monthly_change_percent: float
    ytd_change_dollars: float
    ytd_change_percent: float
    top_holdings: list[Holding]
    monthly_dividends: float
    ytd_dividends: float
    report_date: str
    total_holdings_count: int
    # New fields for multi-account support
    individual_account: Optional[AccountReport] = None
    ira_account: Optional[AccountReport] = None
    combined_retirement_value: float = 0.0  # Individual + IRA total


class PortfolioAnalyzer:
    """Analyzes portfolio data and generates reports."""

    def __init__(self, robinhood_client: RobinhoodClient, snapshot_service: Optional[SnapshotService] = None):
        """
        Initialize the analyzer with an authenticated Robinhood client.

        Args:
            robinhood_client: Authenticated RobinhoodClient instance
            snapshot_service: Optional SnapshotService for storing portfolio history
        """
        self.client = robinhood_client
        self.snapshot_service = snapshot_service

    def analyze(self, top_n: int = 5) -> PortfolioReport:
        """
        Generate a complete portfolio analysis report including all accounts.

        Args:
            top_n: Number of top holdings to include (default 5)

        Returns:
            PortfolioReport with all portfolio metrics
        """
        logger.info("Starting portfolio analysis")

        # Get all accounts to find Individual and IRA
        account_profiles = self.client.get_all_account_profiles()
        logger.info(f"Found {len(account_profiles)} account(s)")

        individual_account = None
        ira_account = None

        for profile in account_profiles:
            account_number = profile.get("account_number", "")
            # Use brokerage_account_type which distinguishes 'individual' from 'ira_roth'
            brokerage_type = profile.get("brokerage_account_type", "").lower()
            account_type = profile.get("type", "").lower()

            logger.info(f"Processing account {account_number}, brokerage_type: {brokerage_type}, type: {account_type}")

            if "ira" in brokerage_type or "roth" in brokerage_type or "retirement" in brokerage_type:
                ira_account = self._analyze_account(account_number, "roth_ira", top_n)
            else:
                # Default to individual account
                individual_account = self._analyze_account(account_number, "individual", top_n)

        # Get dividend data (dividends are across all accounts)
        dividends = self.client.get_dividends()
        monthly_dividends = self._calculate_monthly_dividends(dividends)
        ytd_dividends = self._calculate_ytd_dividends(dividends)

        # Calculate combined values
        individual_value = individual_account.total_value if individual_account else 0.0
        ira_value = ira_account.total_value if ira_account else 0.0
        combined_retirement_value = individual_value + ira_value

        # Use individual account as the primary for backwards compatibility
        # or combine if both exist
        if individual_account:
            total_value = individual_account.total_value
            cash_balance = individual_account.cash_balance
            monthly_change_dollars = individual_account.monthly_change_dollars
            monthly_change_percent = individual_account.monthly_change_percent
            ytd_change_dollars = individual_account.ytd_change_dollars
            ytd_change_percent = individual_account.ytd_change_percent
            top_holdings = individual_account.top_holdings
            total_holdings_count = individual_account.total_holdings_count
        else:
            # Fallback to legacy method if no accounts found via profiles
            logger.warning("No account profiles found, using legacy method")
            portfolio_value = self.client.get_portfolio_value()
            total_value = portfolio_value["equity"]
            cash_balance = portfolio_value["cash"]

            holdings_data = self.client.get_holdings()
            holdings = self._convert_holdings(holdings_data)
            top_holdings = holdings  # Show all holdings
            total_holdings_count = len(holdings)

            monthly_change = self._calculate_period_change("month")
            ytd_change = self._calculate_period_change("year")
            monthly_change_dollars = monthly_change["dollars"]
            monthly_change_percent = monthly_change["percent"]
            ytd_change_dollars = ytd_change["dollars"]
            ytd_change_percent = ytd_change["percent"]

        report = PortfolioReport(
            total_value=round(total_value, 2),
            cash_balance=round(cash_balance, 2),
            monthly_change_dollars=round(monthly_change_dollars, 2),
            monthly_change_percent=round(monthly_change_percent, 2),
            ytd_change_dollars=round(ytd_change_dollars, 2),
            ytd_change_percent=round(ytd_change_percent, 2),
            top_holdings=top_holdings,
            monthly_dividends=round(monthly_dividends, 2),
            ytd_dividends=round(ytd_dividends, 2),
            report_date=datetime.now().strftime("%B %Y"),
            total_holdings_count=total_holdings_count,
            individual_account=individual_account,
            ira_account=ira_account,
            combined_retirement_value=round(combined_retirement_value, 2),
        )

        logger.info(f"Portfolio analysis complete. Individual: ${individual_value:,.2f}, IRA: ${ira_value:,.2f}, Combined: ${combined_retirement_value:,.2f}")
        return report

    def _analyze_account(self, account_number: str, account_type: str, top_n: int) -> AccountReport:
        """
        Analyze a specific account.

        Args:
            account_number: The Robinhood account number
            account_type: Type of account ("individual" or "roth_ira")
            top_n: Number of top holdings to include

        Returns:
            AccountReport for the specified account
        """
        logger.info(f"Analyzing {account_type} account: {account_number}")

        # Get portfolio value for this account
        portfolio_value = self.client.get_portfolio_value_for_account(account_number)
        total_value = portfolio_value["equity"]
        cash_balance = portfolio_value["cash"]

        # Save snapshot for this account (used for future period calculations)
        if self.snapshot_service:
            self.snapshot_service.save_snapshot(account_number, total_value, cash_balance)

        # Get holdings for this account
        holdings_data = self.client.get_holdings_for_account(account_number)
        holdings = self._convert_holdings(holdings_data)
        top_holdings = holdings  # Show all holdings

        # Calculate period changes for this account
        monthly_change = self._calculate_period_change_for_account(account_number, "month")
        ytd_change = self._calculate_period_change_for_account(account_number, "year")

        return AccountReport(
            account_type=account_type,
            account_number=account_number,
            total_value=round(total_value, 2),
            cash_balance=round(cash_balance, 2),
            monthly_change_dollars=round(monthly_change["dollars"], 2),
            monthly_change_percent=round(monthly_change["percent"], 2),
            ytd_change_dollars=round(ytd_change["dollars"], 2),
            ytd_change_percent=round(ytd_change["percent"], 2),
            top_holdings=top_holdings,
            total_holdings_count=len(holdings),
        )

    def _convert_holdings(self, holdings_data: list[dict]) -> list[Holding]:
        """Convert raw holdings data to Holding objects."""
        return [
            Holding(
                symbol=h["symbol"],
                name=h["name"],
                quantity=h["quantity"],
                current_price=h["current_price"],
                average_cost=h["average_buy_price"],
                total_value=h["equity"],
                percent_change=h["percent_change"],
                portfolio_percent=h["percentage_of_portfolio"],
            )
            for h in holdings_data
        ]

    def _calculate_period_change_for_account(self, account_number: str, span: str) -> dict:
        """
        Calculate value change for a given time period for a specific account.

        Uses snapshots if available, falls back to holdings-based calculation.

        Args:
            account_number: The Robinhood account number
            span: Time span ('month', 'year', etc.)

        Returns:
            Dictionary with 'dollars' and 'percent' change
        """
        # First try: Use snapshots if available
        if self.snapshot_service:
            period_change = self._calculate_period_change_from_snapshots(account_number, span)
            if period_change is not None:
                return period_change

        # Second try: Use holdings-based calculation (all-time performance)
        historical = self.client.get_historical_portfolio_for_account(account_number, span=span)

        if historical:
            return {
                "dollars": historical["change_dollars"],
                "percent": historical["change_percent"],
            }

        # Fallback if no data available
        logger.warning(f"No historical data available for account {account_number}, span {span}")
        return {"dollars": 0.0, "percent": 0.0}

    def _calculate_period_change_from_snapshots(self, account_number: str, span: str) -> Optional[dict]:
        """
        Calculate period change using stored snapshots.

        Args:
            account_number: The Robinhood account number
            span: Time span ('month', 'year', etc.)

        Returns:
            Dictionary with 'dollars' and 'percent' change, or None if snapshots unavailable
        """
        today = date.today()

        # Determine start date based on span
        if span == "month":
            start_date = today.replace(day=1)
        elif span == "year":
            start_date = today.replace(month=1, day=1)
        else:
            # For other spans, use the historical method
            return None

        # Get snapshots for start and end dates
        try:
            period_change = self.snapshot_service.calculate_period_change(
                account_number, start_date, today
            )

            if period_change:
                logger.info(
                    f"Calculated {span} change for {account_number} from snapshots: "
                    f"{period_change['change_percent']:.2f}%"
                )
                return {
                    "dollars": period_change["change_dollars"],
                    "percent": period_change["change_percent"],
                }
        except Exception as e:
            logger.error(f"Error calculating period change from snapshots: {str(e)}")

        return None

    def _calculate_period_change(self, span: str) -> dict:
        """
        Calculate value change for a given time period.

        Args:
            span: Time span ('month', 'year', etc.)

        Returns:
            Dictionary with 'dollars' and 'percent' change
        """
        historical = self.client.get_historical_portfolio_value(span=span)

        if historical:
            return {
                "dollars": historical["change_dollars"],
                "percent": historical["change_percent"],
            }

        # Fallback if historical data unavailable
        return {"dollars": 0.0, "percent": 0.0}

    def _calculate_monthly_dividends(self, dividends: list[dict]) -> float:
        """
        Calculate total dividends received in the current month.

        Args:
            dividends: List of dividend records

        Returns:
            Total dividend amount for the month
        """
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total = 0.0
        for div in dividends:
            paid_at = div.get("paid_at")
            if paid_at:
                try:
                    paid_date = datetime.fromisoformat(paid_at.replace("Z", "+00:00"))
                    if paid_date.replace(tzinfo=None) >= month_start:
                        total += div.get("amount", 0)
                except (ValueError, TypeError):
                    continue

        return total

    def _calculate_ytd_dividends(self, dividends: list[dict]) -> float:
        """
        Calculate total dividends received year-to-date.

        Args:
            dividends: List of dividend records

        Returns:
            Total dividend amount for the year
        """
        year_start = datetime(datetime.now().year, 1, 1)

        total = 0.0
        for div in dividends:
            paid_at = div.get("paid_at")
            if paid_at:
                try:
                    paid_date = datetime.fromisoformat(paid_at.replace("Z", "+00:00"))
                    if paid_date.replace(tzinfo=None) >= year_start:
                        total += div.get("amount", 0)
                except (ValueError, TypeError):
                    continue

        return total
