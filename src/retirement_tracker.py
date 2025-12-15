"""
Retirement goal tracking and projections.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RetirementConfig:
    """Configuration for retirement goal tracking."""

    target_amount: float
    target_age: int
    current_age: int = 0  # Will be calculated from birth_year if provided
    monthly_contribution: float = 0.0
    assumed_annual_return: float = 0.07  # 7% default
    birth_year: Optional[int] = None  # If provided, current_age is calculated automatically

    def __post_init__(self):
        """Calculate current_age from birth_year if provided."""
        if self.birth_year is not None:
            current_year = datetime.now().year
            self.current_age = current_year - self.birth_year
            logger.info(f"Calculated age {self.current_age} from birth year {self.birth_year}")


@dataclass
class RetirementProgress:
    """Current progress toward retirement goal."""

    current_value: float
    target_amount: float
    percent_complete: float
    years_remaining: int
    monthly_needed: float  # Monthly savings needed to reach goal
    on_track: bool
    projected_value: float  # Projected value at target age
    surplus_or_deficit: float  # Positive = surplus, negative = deficit


class RetirementTracker:
    """Tracks progress toward retirement goals with projections."""

    def __init__(self, config: RetirementConfig):
        """
        Initialize the retirement tracker.

        Args:
            config: RetirementConfig with goal parameters
        """
        self.config = config

    def calculate_progress(self, current_portfolio_value: float) -> RetirementProgress:
        """
        Calculate retirement progress and projections.

        Args:
            current_portfolio_value: Current total portfolio value

        Returns:
            RetirementProgress with all calculated metrics
        """
        years_remaining = max(0, self.config.target_age - self.config.current_age)
        percent_complete = (current_portfolio_value / self.config.target_amount) * 100

        # Project future value at target age with current contribution rate
        projected_value = self._project_future_value(
            current=current_portfolio_value,
            monthly_contribution=self.config.monthly_contribution,
            years=years_remaining,
            annual_return=self.config.assumed_annual_return,
        )

        # Calculate monthly needed to reach goal
        monthly_needed = self._calculate_monthly_needed(
            current=current_portfolio_value,
            target=self.config.target_amount,
            years=years_remaining,
            annual_return=self.config.assumed_annual_return,
        )

        on_track = projected_value >= self.config.target_amount
        surplus_or_deficit = projected_value - self.config.target_amount

        logger.info(
            f"Retirement progress: {percent_complete:.1f}% complete, "
            f"{'on track' if on_track else 'behind'}"
        )

        return RetirementProgress(
            current_value=round(current_portfolio_value, 2),
            target_amount=round(self.config.target_amount, 2),
            percent_complete=round(percent_complete, 1),
            years_remaining=years_remaining,
            monthly_needed=round(max(0, monthly_needed), 2),
            on_track=on_track,
            projected_value=round(projected_value, 2),
            surplus_or_deficit=round(surplus_or_deficit, 2),
        )

    def _project_future_value(
        self,
        current: float,
        monthly_contribution: float,
        years: int,
        annual_return: float,
    ) -> float:
        """
        Project future value with compound growth and regular contributions.

        Uses the future value formula:
        FV = PV * (1 + r)^n + PMT * ((1 + r)^n - 1) / r

        Where:
        - PV = present value (current portfolio)
        - r = monthly return rate
        - n = number of months
        - PMT = monthly contribution

        Args:
            current: Current portfolio value
            monthly_contribution: Monthly contribution amount
            years: Years until retirement
            annual_return: Assumed annual return rate (e.g., 0.07 for 7%)

        Returns:
            Projected portfolio value at retirement
        """
        if years <= 0:
            return current

        months = years * 12
        monthly_return = annual_return / 12

        # Future value of current portfolio
        fv_current = current * ((1 + monthly_return) ** months)

        # Future value of monthly contributions (annuity)
        if monthly_return > 0 and monthly_contribution > 0:
            fv_contributions = monthly_contribution * (
                ((1 + monthly_return) ** months - 1) / monthly_return
            )
        else:
            fv_contributions = monthly_contribution * months

        return fv_current + fv_contributions

    def _calculate_monthly_needed(
        self,
        current: float,
        target: float,
        years: int,
        annual_return: float,
    ) -> float:
        """
        Calculate the monthly contribution needed to reach target.

        Rearranges the future value formula to solve for PMT:
        PMT = (FV - PV * (1 + r)^n) * r / ((1 + r)^n - 1)

        Args:
            current: Current portfolio value
            target: Target retirement amount
            years: Years until retirement
            annual_return: Assumed annual return rate

        Returns:
            Required monthly contribution to reach target
        """
        if years <= 0:
            # If already at or past target age, return remaining needed
            return max(0, target - current)

        months = years * 12
        monthly_return = annual_return / 12

        # Future value of current portfolio
        fv_current = current * ((1 + monthly_return) ** months)

        # Amount needed from contributions
        needed_from_contributions = target - fv_current

        if needed_from_contributions <= 0:
            # Already on track without additional contributions
            return 0

        # Solve for monthly payment
        if monthly_return > 0:
            monthly_needed = needed_from_contributions * monthly_return / (
                (1 + monthly_return) ** months - 1
            )
        else:
            monthly_needed = needed_from_contributions / months

        return monthly_needed
