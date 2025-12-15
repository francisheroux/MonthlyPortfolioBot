"""
Robinhood API client with session caching support.

Since Robinhood removed TOTP authentication in Dec 2024, this client now
supports session caching via S3 for automated Lambda execution.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError
import robin_stocks.robinhood as rh

logger = logging.getLogger(__name__)

# Default pickle filename used by robin_stocks
# robin_stocks stores at: ~/.tokens/robinhood{pickle_name}.pickle
# So with empty string, it's: ~/.tokens/robinhood.pickle
PICKLE_FILENAME = "robinhood.pickle"  # This is the S3 key name
PICKLE_NAME_SUFFIX = ""  # robin_stocks suffix - empty means ~/.tokens/robinhood.pickle


class RobinhoodClient:
    """Client for interacting with Robinhood API with session caching."""

    def __init__(
        self,
        username: str,
        password: str,
        totp_secret: Optional[str] = None,
    ):
        """
        Initialize the Robinhood client.

        Args:
            username: Robinhood account email
            password: Robinhood account password
            totp_secret: Base32 TOTP secret for MFA (deprecated - Robinhood removed TOTP)
        """
        self.username = username
        self.password = password
        self.totp_secret = totp_secret
        self._logged_in = False

    def login(self, store_session: bool = True) -> bool:
        """
        Authenticate with Robinhood.

        First tries to use cached session from pickle file.
        If that fails, initiates new login (requires device approval).

        Args:
            store_session: Whether to save the session for future use

        Returns:
            True if login successful, False otherwise
        """
        try:
            logger.info(f"Attempting login for {self.username}")

            mfa_code = None
            if self.totp_secret:
                # Generate fresh TOTP code from stored secret (if available)
                import pyotp
                totp = pyotp.TOTP(self.totp_secret)
                mfa_code = totp.now()
                logger.info("Generated TOTP code for MFA")

            # Login - robin_stocks handles the challenge/verification flow
            # store_session=True allows it to cache the session after device approval
            # IMPORTANT: pickle_name is a SUFFIX, not a path!
            # robin_stocks stores at: ~/.tokens/robinhood{pickle_name}.pickle
            result = rh.login(
                username=self.username,
                password=self.password,
                mfa_code=mfa_code,
                store_session=store_session,
                pickle_name=PICKLE_NAME_SUFFIX,
            )

            logger.info(f"Login result type: {type(result)}, value: {result}")

            # Check if login succeeded - result can be dict with access_token or other formats
            if result:
                if isinstance(result, dict):
                    if result.get('access_token'):
                        self._logged_in = True
                        logger.info("Successfully logged into Robinhood (got access_token)")
                        return True
                    elif result.get('detail'):
                        # Sometimes returns {'detail': 'logged in with cached token'} or similar
                        self._logged_in = True
                        logger.info(f"Successfully logged into Robinhood: {result.get('detail')}")
                        return True
                else:
                    # Non-dict truthy result - assume success
                    self._logged_in = True
                    logger.info("Successfully logged into Robinhood")
                    return True

            logger.error("Login returned None or falsy - authentication failed")
            return False

        except Exception as e:
            logger.error(f"Login failed with exception: {str(e)}")
            return False

    @staticmethod
    def download_session_from_s3(bucket: str, local_path: str = "/tmp/robinhood.pickle") -> bool:
        """
        Download session pickle file from S3.

        Args:
            bucket: S3 bucket name
            local_path: Local path to save the pickle file

        Returns:
            True if download successful, False otherwise
        """
        try:
            s3 = boto3.client("s3")
            s3.download_file(bucket, PICKLE_FILENAME, local_path)
            logger.info(f"Downloaded session from s3://{bucket}/{PICKLE_FILENAME}")
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404" or error_code == "NoSuchKey":
                logger.warning(f"No session file found in S3 bucket {bucket}")
            else:
                logger.error(f"Failed to download session from S3: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Failed to download session from S3: {str(e)}")
            return False

    @staticmethod
    def upload_session_to_s3(bucket: str, local_path: str = "robinhood.pickle") -> bool:
        """
        Upload session pickle file to S3.

        Args:
            bucket: S3 bucket name
            local_path: Local path to the pickle file

        Returns:
            True if upload successful, False otherwise
        """
        try:
            if not os.path.exists(local_path):
                logger.error(f"Session file not found at {local_path}")
                return False

            s3 = boto3.client("s3")
            s3.upload_file(local_path, bucket, PICKLE_FILENAME)
            logger.info(f"Uploaded session to s3://{bucket}/{PICKLE_FILENAME}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload session to S3: {str(e)}")
            return False

    @staticmethod
    def get_pickle_path() -> str:
        """Get the actual pickle file path used by robin_stocks."""
        # robin_stocks stores pickle at: ~/.tokens/robinhood{suffix}.pickle
        # With empty suffix, it's: ~/.tokens/robinhood.pickle
        tokens_dir = Path.home() / ".tokens"
        return str(tokens_dir / f"robinhood{PICKLE_NAME_SUFFIX}.pickle")

    def logout(self) -> None:
        """Logout from Robinhood and clean up session."""
        try:
            rh.logout()
            self._logged_in = False
            logger.info("Successfully logged out from Robinhood")
        except Exception as e:
            logger.warning(f"Logout encountered an error: {str(e)}")

    def get_portfolio_value(self) -> dict:
        """
        Get total portfolio value and equity breakdown.

        Returns:
            Dictionary with portfolio value information
        """
        self._ensure_logged_in()

        try:
            # Get portfolio profile
            portfolio = rh.profiles.load_portfolio_profile()

            # Get account profile for additional details
            account = rh.profiles.load_account_profile()

            return {
                "equity": float(portfolio.get("equity", 0) or 0),
                "extended_hours_equity": float(portfolio.get("extended_hours_equity", 0) or 0),
                "market_value": float(portfolio.get("market_value", 0) or 0),
                "cash": float(account.get("portfolio_cash", 0) or 0),
                "buying_power": float(account.get("buying_power", 0) or 0),
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio value: {str(e)}")
            raise

    def get_holdings(self) -> list[dict]:
        """
        Get all stock holdings with position details.

        Returns:
            List of holdings with symbol, quantity, value, etc.
        """
        self._ensure_logged_in()

        try:
            # Get all positions
            positions = rh.account.build_holdings()

            holdings = []
            for symbol, data in positions.items():
                holdings.append({
                    "symbol": symbol,
                    "name": data.get("name", ""),
                    "quantity": float(data.get("quantity", 0) or 0),
                    "average_buy_price": float(data.get("average_buy_price", 0) or 0),
                    "current_price": float(data.get("price", 0) or 0),
                    "equity": float(data.get("equity", 0) or 0),
                    "percent_change": float(data.get("percent_change", 0) or 0),
                    "equity_change": float(data.get("equity_change", 0) or 0),
                    "percentage_of_portfolio": float(data.get("percentage", 0) or 0),
                })

            # Sort by equity (highest value first)
            holdings.sort(key=lambda x: x["equity"], reverse=True)
            return holdings

        except Exception as e:
            logger.error(f"Failed to get holdings: {str(e)}")
            raise

    def get_dividends(self) -> list[dict]:
        """
        Get dividend payment history.

        Returns:
            List of dividend payments with date and amount
        """
        self._ensure_logged_in()

        try:
            dividends = rh.account.get_dividends()

            result = []
            for div in dividends:
                if div.get("state") == "paid":
                    result.append({
                        "symbol": self._get_symbol_from_instrument(div.get("instrument")),
                        "amount": float(div.get("amount", 0) or 0),
                        "paid_at": div.get("paid_at"),
                        "payable_date": div.get("payable_date"),
                    })

            return result

        except Exception as e:
            logger.error(f"Failed to get dividends: {str(e)}")
            raise

    def get_historical_portfolio_value(self, span: str = "month") -> Optional[dict]:
        """
        Get historical portfolio values for calculating changes.

        Args:
            span: Time span - 'day', 'week', 'month', 'year', '5year', 'all'

        Returns:
            Dictionary with historical data points
        """
        self._ensure_logged_in()

        try:
            # Map requested span to valid API span
            api_span = self._map_span_to_api_span(span)
            logger.info(f"Mapped span '{span}' to API span '{api_span}'")

            # Get historical portfolio data
            try:
                historicals = rh.account.get_historical_portfolio(
                    span=api_span,
                    bounds="regular"
                )
                logger.info(f"Legacy API response type: {type(historicals)}")
                if historicals:
                    logger.info(f"Response keys: {historicals.keys() if isinstance(historicals, dict) else 'not a dict'}")
            except Exception as e:
                logger.error(f"Error calling get_historical_portfolio: {str(e)}")
                return None

            if not historicals or "equity_historicals" not in historicals:
                logger.warning(f"No historical data available for span={api_span}")
                if historicals and isinstance(historicals, dict):
                    logger.info(f"Response keys available: {list(historicals.keys())}")
                return None

            data_points = historicals["equity_historicals"]
            if not data_points:
                return None

            # Get first and last values
            first_value = float(data_points[0].get("adjusted_close_equity", 0) or 0)
            last_value = float(data_points[-1].get("adjusted_close_equity", 0) or 0)

            if first_value <= 0:
                return None

            return {
                "start_value": first_value,
                "end_value": last_value,
                "change_dollars": last_value - first_value,
                "change_percent": ((last_value - first_value) / first_value * 100),
                "data_points": len(data_points),
            }

        except Exception as e:
            logger.error(f"Failed to get historical portfolio: {str(e)}")
            return None

    def _ensure_logged_in(self) -> None:
        """Raise exception if not logged in."""
        if not self._logged_in:
            raise RuntimeError("Not logged in. Call login() first.")

    def _map_span_to_api_span(self, span: str) -> str:
        """
        Map requested span to valid Robinhood API span.

        The Robinhood API get_historical_portfolio() only works with: day, week
        For month/year requests, we use week and estimate proportionally.

        Args:
            span: Requested span ('day', 'week', 'month', 'year', '5year', 'all')

        Returns:
            Valid API span parameter
        """
        # Map to closest available span
        span_mapping = {
            "day": "day",
            "week": "week",
            "month": "week",  # Use week, estimate monthly
            "year": "week",   # Use week, estimate yearly (conservative)
            "5year": "week",
            "all": "week",
        }
        return span_mapping.get(span, "week")

    def _get_symbol_from_instrument(self, instrument_url: Optional[str]) -> str:
        """Get stock symbol from instrument URL."""
        if not instrument_url:
            return "UNKNOWN"

        try:
            instrument = rh.stocks.get_instrument_by_url(instrument_url)
            return instrument.get("symbol", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    def get_all_accounts(self) -> list[dict]:
        """
        Get all linked accounts (Individual + IRA).

        Returns:
            List of account dictionaries with account_number and type
        """
        self._ensure_logged_in()

        try:
            # load_phoenix_account returns a list of all accounts
            phoenix_data = rh.account.load_phoenix_account()

            if not phoenix_data:
                logger.warning("No phoenix account data returned")
                return []

            # Phoenix data should be a list of accounts
            if isinstance(phoenix_data, list):
                accounts = []
                for account in phoenix_data:
                    if isinstance(account, dict):
                        accounts.append({
                            "account_number": account.get("account_number", ""),
                            "type": account.get("type", "unknown"),
                            "equity": float(account.get("portfolio_equity", 0) or 0),
                        })
                return accounts
            elif isinstance(phoenix_data, dict):
                # Sometimes returns single dict for single account
                return [{
                    "account_number": phoenix_data.get("account_number", ""),
                    "type": phoenix_data.get("type", "unknown"),
                    "equity": float(phoenix_data.get("portfolio_equity", 0) or 0),
                }]

            return []

        except Exception as e:
            logger.error(f"Failed to get all accounts: {str(e)}")
            return []

    def get_all_account_profiles(self) -> list[dict]:
        """
        Get all account profiles with detailed information.

        Returns:
            List of account profile dictionaries
        """
        self._ensure_logged_in()

        try:
            # Get all accounts using dataType="regular" which returns paginated results
            profiles = rh.profiles.load_account_profile(dataType="regular")

            if not profiles:
                logger.warning("No account profiles returned")
                return []

            # dataType="regular" returns paginated results with 'results' key
            if isinstance(profiles, dict) and "results" in profiles:
                return profiles.get("results", [])
            elif isinstance(profiles, list):
                return profiles
            elif isinstance(profiles, dict):
                return [profiles]

            return []

        except Exception as e:
            logger.error(f"Failed to get account profiles: {str(e)}")
            return []

    def get_portfolio_value_for_account(self, account_number: str) -> dict:
        """
        Get portfolio value for a specific account.

        Args:
            account_number: The Robinhood account number

        Returns:
            Dictionary with portfolio value information
        """
        self._ensure_logged_in()

        try:
            # Get portfolio profile for specific account
            portfolio = rh.profiles.load_portfolio_profile(account_number=account_number)

            # Get account profile for additional details
            account = rh.profiles.load_account_profile(account_number=account_number)

            if not portfolio:
                logger.warning(f"No portfolio data for account {account_number}")
                return {
                    "equity": 0.0,
                    "extended_hours_equity": 0.0,
                    "market_value": 0.0,
                    "cash": 0.0,
                    "buying_power": 0.0,
                    "account_number": account_number,
                }

            return {
                "equity": float(portfolio.get("equity", 0) or 0),
                "extended_hours_equity": float(portfolio.get("extended_hours_equity", 0) or 0),
                "market_value": float(portfolio.get("market_value", 0) or 0),
                "cash": float(account.get("portfolio_cash", 0) or 0) if account else 0.0,
                "buying_power": float(account.get("buying_power", 0) or 0) if account else 0.0,
                "account_number": account_number,
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio value for account {account_number}: {str(e)}")
            raise

    def get_holdings_for_account(self, account_number: str) -> list[dict]:
        """
        Get all stock holdings for a specific account.

        Args:
            account_number: The Robinhood account number

        Returns:
            List of holdings with symbol, quantity, value, etc.
        """
        self._ensure_logged_in()

        try:
            # Use get_open_stock_positions which properly supports account_number parameter
            # (get_all_positions doesn't support account_number and only returns default account)
            account_positions = rh.account.get_open_stock_positions(account_number=account_number)

            if not account_positions:
                logger.info(f"No holdings for account {account_number}")
                return []

            # Get current prices for all symbols
            symbols = [pos.get("symbol", "") for pos in account_positions if pos.get("symbol")]
            quotes = {}
            if symbols:
                quote_data = rh.stocks.get_quotes(symbols)
                if quote_data:
                    for q in quote_data:
                        if q and q.get("symbol"):
                            quotes[q["symbol"]] = float(q.get("last_trade_price", 0) or 0)

            # Get portfolio value for calculating percentages
            portfolio = rh.profiles.load_portfolio_profile(account_number=account_number)
            total_equity = float(portfolio.get("equity", 1) or 1) if portfolio else 1.0

            holdings = []
            for pos in account_positions:
                symbol = pos.get("symbol", "")
                quantity = float(pos.get("quantity", 0) or 0)
                avg_buy_price = float(pos.get("average_buy_price", 0) or 0)
                current_price = quotes.get(symbol, avg_buy_price)
                equity = quantity * current_price
                cost_basis = quantity * avg_buy_price

                percent_change = 0.0
                if cost_basis > 0:
                    percent_change = ((equity - cost_basis) / cost_basis) * 100

                equity_change = equity - cost_basis
                portfolio_percent = (equity / total_equity * 100) if total_equity > 0 else 0

                # Get stock name
                name = ""
                try:
                    instrument_url = pos.get("instrument")
                    if instrument_url:
                        instrument = rh.stocks.get_instrument_by_url(instrument_url)
                        name = instrument.get("name", "") if instrument else ""
                except Exception:
                    pass

                holdings.append({
                    "symbol": symbol,
                    "name": name,
                    "quantity": quantity,
                    "average_buy_price": avg_buy_price,
                    "current_price": current_price,
                    "equity": equity,
                    "percent_change": percent_change,
                    "equity_change": equity_change,
                    "percentage_of_portfolio": portfolio_percent,
                })

            # Sort by equity (highest value first)
            holdings.sort(key=lambda x: x["equity"], reverse=True)
            return holdings

        except Exception as e:
            logger.error(f"Failed to get holdings for account {account_number}: {str(e)}")
            return []

    def calculate_account_performance(self, account_number: str) -> Optional[dict]:
        """
        Calculate account performance using holdings data (cost basis vs current value).

        This is a hybrid approach that works around the API limitation that doesn't
        support per-account historical data. It calculates performance based on the
        cost basis of all positions and their current values.

        Args:
            account_number: The Robinhood account number

        Returns:
            Dictionary with performance metrics or None if calculation fails
        """
        self._ensure_logged_in()

        try:
            # Get current portfolio value and holdings
            portfolio = self.get_portfolio_value_for_account(account_number)
            holdings = self.get_holdings_for_account(account_number)

            if not portfolio:
                logger.warning(f"Could not get portfolio value for account {account_number}")
                return None

            current_equity = portfolio.get("equity", 0)

            # Calculate cost basis and total gain/loss from holdings
            total_cost_basis = 0.0
            total_current_value = 0.0
            total_gain_loss = 0.0

            for holding in holdings:
                quantity = holding.get("quantity", 0)
                avg_cost = holding.get("average_buy_price", 0)
                current_price = holding.get("current_price", 0)

                cost = quantity * avg_cost
                current_val = quantity * current_price

                total_cost_basis += cost
                total_current_value += current_val
                total_gain_loss += current_val - cost

            # Add cash to current value for accurate total
            cash = portfolio.get("cash", 0)
            total_current_with_cash = total_current_value + cash

            # Calculate percentage change
            if total_cost_basis > 0:
                percent_change = (total_gain_loss / total_cost_basis) * 100
            else:
                percent_change = 0.0

            return {
                "cost_basis": total_cost_basis,
                "current_value": total_current_with_cash,
                "gain_loss_dollars": total_gain_loss,
                "gain_loss_percent": percent_change,
                "holdings_count": len(holdings),
            }

        except Exception as e:
            logger.error(f"Failed to calculate account performance for {account_number}: {str(e)}")
            return None

    def get_historical_portfolio_for_account(
        self, account_number: str, span: str = "month"
    ) -> Optional[dict]:
        """
        Get historical portfolio values for a specific account using holdings-based calculation.

        Since the Robinhood API doesn't provide per-account historical data or reliable
        portfolio historical data, this method calculates performance using current
        holdings data (cost basis vs current value).

        For period-specific returns (month/year), this returns all-time performance
        which reflects the actual account performance.

        Args:
            account_number: The Robinhood account number
            span: Time span - 'day', 'week', 'month', 'year', '5year', 'all'

        Returns:
            Dictionary with historical data points calculated from holdings
        """
        self._ensure_logged_in()

        try:
            # Use holdings-based calculation for reliable per-account performance
            holdings_perf = self.calculate_account_performance(account_number)

            if not holdings_perf:
                logger.warning(f"Could not calculate account performance for {account_number}")
                return None

            # Return holdings-based performance
            # This is the most accurate method available given API limitations
            logger.info(
                f"Account {account_number} performance: "
                f"{holdings_perf['gain_loss_percent']:.2f}% "
                f"(cost: ${holdings_perf['cost_basis']:.2f}, current: ${holdings_perf['current_value']:.2f})"
            )

            return {
                "start_value": holdings_perf["cost_basis"],
                "end_value": holdings_perf["current_value"],
                "change_dollars": holdings_perf["gain_loss_dollars"],
                "change_percent": holdings_perf["gain_loss_percent"],
                "data_points": holdings_perf["holdings_count"],
                "method": "holdings_based",
            }

        except Exception as e:
            logger.error(f"Failed to get historical portfolio for account {account_number}: {str(e)}")
            return None
