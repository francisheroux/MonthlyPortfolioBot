"""
Email service for sending newsletters via Gmail SMTP or AWS SES.
"""

import logging
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from jinja2 import Environment, FileSystemLoader

from .portfolio_analyzer import PortfolioReport
from .retirement_tracker import RetirementProgress

logger = logging.getLogger(__name__)


class EmailService:
    """Sends HTML newsletters via Gmail SMTP or AWS SES."""

    def __init__(
        self,
        sender_email: str,
        recipient_email: str,
        gmail_app_password: Optional[str] = None,
        region: str = "us-east-1",
    ):
        """
        Initialize the email service.

        Args:
            sender_email: Sender email address
            recipient_email: Recipient email address
            gmail_app_password: Gmail App Password (if using Gmail SMTP)
            region: AWS region for SES (if using SES)
        """
        self.sender = sender_email
        self.sender_address = self._extract_email_address(sender_email)
        self.recipient = recipient_email
        self.gmail_app_password = gmail_app_password
        self.use_gmail = gmail_app_password is not None

        if not self.use_gmail:
            self.ses_client = boto3.client("ses", region_name=region)

        # Set up Jinja2 template environment
        template_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=True,
        )

    def _extract_email_address(self, email: str) -> str:
        """Extract email address from format like 'Name <email@domain.com>'."""
        match = re.search(r'<([^>]+)>', email)
        if match:
            return match.group(1)
        return email.strip()

    def send_newsletter(
        self,
        portfolio_report: PortfolioReport,
        retirement_progress: RetirementProgress,
    ) -> bool:
        """
        Generate and send the monthly newsletter.

        Args:
            portfolio_report: Portfolio analysis data
            retirement_progress: Retirement tracking data

        Returns:
            True if email sent successfully, False otherwise
        """
        subject = f"Your Monthly Portfolio Summary - {portfolio_report.report_date}"

        html_body = self._render_template(portfolio_report, retirement_progress)

        return self._send_email(subject, html_body)

    def _render_template(
        self,
        portfolio_report: PortfolioReport,
        retirement_progress: RetirementProgress,
    ) -> str:
        """
        Render the HTML email template with data.

        Args:
            portfolio_report: Portfolio analysis data
            retirement_progress: Retirement tracking data

        Returns:
            Rendered HTML string
        """
        template = self.jinja_env.get_template("newsletter.html")

        return template.render(
            # Portfolio data
            report_date=portfolio_report.report_date,
            total_value=portfolio_report.total_value,
            cash_balance=portfolio_report.cash_balance,
            monthly_change_dollars=portfolio_report.monthly_change_dollars,
            monthly_change_percent=portfolio_report.monthly_change_percent,
            ytd_change_dollars=portfolio_report.ytd_change_dollars,
            ytd_change_percent=portfolio_report.ytd_change_percent,
            top_holdings=portfolio_report.top_holdings,
            total_holdings_count=portfolio_report.total_holdings_count,
            monthly_dividends=portfolio_report.monthly_dividends,
            ytd_dividends=portfolio_report.ytd_dividends,
            # IRA account data
            ira_account=portfolio_report.ira_account,
            combined_retirement_value=portfolio_report.combined_retirement_value,
            # Retirement data
            retirement=retirement_progress,
        )

    def _html_to_plain_text(self, html: str) -> str:
        """
        Convert HTML to plain text for multipart emails.

        Args:
            html: HTML content

        Returns:
            Plain text version of the content
        """
        text = html
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</td>', ' | ', text, flags=re.IGNORECASE)
        text = re.sub(r'</th>', ' | ', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'&#\d+;', '', text)
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = '\n'.join(line.strip() for line in text.split('\n'))

        return text.strip()

    def _send_email(self, subject: str, html_body: str) -> bool:
        """
        Send email via Gmail SMTP or AWS SES.

        Args:
            subject: Email subject line
            html_body: HTML content of the email

        Returns:
            True if sent successfully, False otherwise
        """
        if self.use_gmail:
            return self._send_via_gmail(subject, html_body)
        else:
            return self._send_via_ses(subject, html_body)

    def _send_via_gmail(self, subject: str, html_body: str) -> bool:
        """
        Send email via Gmail SMTP.

        Args:
            subject: Email subject line
            html_body: HTML content of the email

        Returns:
            True if sent successfully, False otherwise
        """
        plain_text_body = self._html_to_plain_text(html_body)

        # Create multipart message
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self.sender
        message["To"] = self.recipient

        # Attach plain text and HTML versions
        part1 = MIMEText(plain_text_body, "plain")
        part2 = MIMEText(html_body, "html")
        message.attach(part1)
        message.attach(part2)

        try:
            # Create secure SSL context
            context = ssl.create_default_context()

            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
                server.login(self.sender_address, self.gmail_app_password)
                server.sendmail(
                    self.sender_address,
                    self.recipient,
                    message.as_string()
                )

            logger.info(f"Email sent successfully via Gmail SMTP")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Gmail authentication failed. Check your App Password: {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to send email via Gmail: {str(e)}")
            return False

    def _send_via_ses(self, subject: str, html_body: str) -> bool:
        """
        Send email via AWS SES with both HTML and plain text versions.

        Args:
            subject: Email subject line
            html_body: HTML content of the email

        Returns:
            True if sent successfully, False otherwise
        """
        plain_text_body = self._html_to_plain_text(html_body)

        try:
            response = self.ses_client.send_email(
                Source=self.sender,
                Destination={
                    "ToAddresses": [self.recipient],
                },
                Message={
                    "Subject": {
                        "Data": subject,
                        "Charset": "UTF-8",
                    },
                    "Body": {
                        "Text": {
                            "Data": plain_text_body,
                            "Charset": "UTF-8",
                        },
                        "Html": {
                            "Data": html_body,
                            "Charset": "UTF-8",
                        },
                    },
                },
            )

            message_id = response.get("MessageId")
            logger.info(f"Email sent successfully via SES. Message ID: {message_id}")
            return True

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"Failed to send email. Error: {error_code} - {error_message}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error sending email: {str(e)}")
            return False

    def send_test_email(self) -> bool:
        """
        Send a simple test email to verify configuration.

        Returns:
            True if sent successfully, False otherwise
        """
        subject = "Portfolio Bot - Test Email"
        html_body = """
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h1 style="color: #00C805;">Test Email Successful!</h1>
            <p>Your email configuration is working correctly.</p>
            <p>The Monthly Portfolio Bot will send your newsletter on the 1st of each month.</p>
        </body>
        </html>
        """

        return self._send_email(subject, html_body)
