"""
AWS SES Email Authentication Setup Script

This script automatically configures:
1. SES domain verification with DKIM
2. Route53 DNS records (SPF, DKIM, DMARC)
3. Custom MAIL FROM domain

Run this once to set up proper email authentication and prevent spam flagging.
"""

import argparse
import sys
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError


def get_domain_from_email(email: str) -> str:
    """Extract domain from email address."""
    return email.split("@")[1]


def get_hosted_zone_id(route53_client, domain: str) -> Optional[str]:
    """Find the Route53 hosted zone ID for a domain."""
    try:
        response = route53_client.list_hosted_zones_by_name(DNSName=domain)
        for zone in response.get("HostedZones", []):
            zone_name = zone["Name"].rstrip(".")
            if zone_name == domain:
                return zone["Id"].split("/")[-1]
        return None
    except ClientError as e:
        print(f"Error finding hosted zone: {e}")
        return None


def verify_domain_identity(ses_client, domain: str) -> bool:
    """Verify domain identity in SES."""
    try:
        ses_client.verify_domain_identity(Domain=domain)
        print(f"✓ Domain identity verification initiated for: {domain}")
        return True
    except ClientError as e:
        if "already verified" in str(e).lower():
            print(f"✓ Domain {domain} is already verified")
            return True
        print(f"✗ Error verifying domain: {e}")
        return False


def setup_dkim(ses_client, domain: str) -> list[str]:
    """Enable DKIM for domain and return DKIM tokens."""
    try:
        response = ses_client.verify_domain_dkim(Domain=domain)
        tokens = response.get("DkimTokens", [])
        print(f"✓ DKIM enabled. Got {len(tokens)} DKIM tokens")
        return tokens
    except ClientError as e:
        print(f"✗ Error setting up DKIM: {e}")
        return []


def setup_mail_from_domain(ses_client, domain: str, mail_from_subdomain: str = "mail") -> bool:
    """Set up custom MAIL FROM domain."""
    mail_from_domain = f"{mail_from_subdomain}.{domain}"
    try:
        ses_client.set_identity_mail_from_domain(
            Identity=domain,
            MailFromDomain=mail_from_domain,
            BehaviorOnMXFailure="UseDefaultValue"
        )
        print(f"✓ Custom MAIL FROM domain set: {mail_from_domain}")
        return True
    except ClientError as e:
        print(f"✗ Error setting MAIL FROM domain: {e}")
        return False


def create_dns_records(
    route53_client,
    hosted_zone_id: str,
    domain: str,
    dkim_tokens: list[str],
    dmarc_email: str,
    region: str = "us-east-1"
) -> bool:
    """Create all necessary DNS records in Route53."""

    changes = []

    # SPF Record for main domain
    changes.append({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": domain,
            "Type": "TXT",
            "TTL": 300,
            "ResourceRecords": [
                {"Value": '"v=spf1 include:amazonses.com ~all"'}
            ]
        }
    })
    print(f"  → Adding SPF record for {domain}")

    # DKIM CNAME Records
    for token in dkim_tokens:
        changes.append({
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": f"{token}._domainkey.{domain}",
                "Type": "CNAME",
                "TTL": 300,
                "ResourceRecords": [
                    {"Value": f"{token}.dkim.amazonses.com"}
                ]
            }
        })
        print(f"  → Adding DKIM CNAME for {token}._domainkey.{domain}")

    # DMARC Record
    changes.append({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": f"_dmarc.{domain}",
            "Type": "TXT",
            "TTL": 300,
            "ResourceRecords": [
                {"Value": f'"v=DMARC1; p=none; rua=mailto:{dmarc_email}"'}
            ]
        }
    })
    print(f"  → Adding DMARC record for _dmarc.{domain}")

    # MX Record for custom MAIL FROM domain
    mail_from_domain = f"mail.{domain}"
    changes.append({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": mail_from_domain,
            "Type": "MX",
            "TTL": 300,
            "ResourceRecords": [
                {"Value": f"10 feedback-smtp.{region}.amazonses.com"}
            ]
        }
    })
    print(f"  → Adding MX record for {mail_from_domain}")

    # SPF Record for MAIL FROM subdomain
    changes.append({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": mail_from_domain,
            "Type": "TXT",
            "TTL": 300,
            "ResourceRecords": [
                {"Value": '"v=spf1 include:amazonses.com ~all"'}
            ]
        }
    })
    print(f"  → Adding SPF record for {mail_from_domain}")

    try:
        route53_client.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Comment": "SES email authentication records",
                "Changes": changes
            }
        )
        print(f"✓ All DNS records created successfully")
        return True
    except ClientError as e:
        print(f"✗ Error creating DNS records: {e}")
        return False


def check_dkim_status(ses_client, domain: str, max_attempts: int = 12) -> bool:
    """Wait for DKIM verification to complete."""
    print(f"\nWaiting for DKIM verification (this can take 1-5 minutes)...")

    for attempt in range(max_attempts):
        try:
            response = ses_client.get_identity_dkim_attributes(Identities=[domain])
            attributes = response.get("DkimAttributes", {}).get(domain, {})
            status = attributes.get("DkimVerificationStatus", "Pending")

            if status == "Success":
                print(f"✓ DKIM verification successful!")
                return True
            elif status == "Failed":
                print(f"✗ DKIM verification failed")
                return False
            else:
                print(f"  Status: {status} (attempt {attempt + 1}/{max_attempts})")
                time.sleep(30)
        except ClientError as e:
            print(f"  Error checking status: {e}")
            time.sleep(30)

    print("⚠ DKIM verification still pending. It may complete in a few more minutes.")
    return False


def check_ses_sandbox_status(ses_client) -> None:
    """Check if account is in SES sandbox mode."""
    try:
        response = ses_client.get_account()
        enforcement_status = response.get("EnforcementStatus", "UNKNOWN")

        if enforcement_status == "HEALTHY":
            print("✓ SES account is in production mode")
        else:
            print(f"⚠ SES account status: {enforcement_status}")
            print("  If in sandbox mode, request production access in AWS Console:")
            print("  https://console.aws.amazon.com/ses/home#/account")
    except ClientError:
        # get_account may not be available in all regions/accounts
        print("⚠ Could not determine SES sandbox status")
        print("  Check manually: https://console.aws.amazon.com/ses/home#/account")


def main():
    parser = argparse.ArgumentParser(
        description="Set up AWS SES email authentication to prevent spam flagging"
    )
    parser.add_argument(
        "--sender-email",
        required=True,
        help="Sender email address (e.g., portfolio@yourdomain.com)"
    )
    parser.add_argument(
        "--dmarc-email",
        help="Email to receive DMARC reports (defaults to sender email)"
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region (default: us-east-1)"
    )
    parser.add_argument(
        "--skip-dns",
        action="store_true",
        help="Skip Route53 DNS record creation (if not using Route53)"
    )
    parser.add_argument(
        "--wait-for-dkim",
        action="store_true",
        help="Wait for DKIM verification to complete"
    )

    args = parser.parse_args()

    domain = get_domain_from_email(args.sender_email)
    dmarc_email = args.dmarc_email or args.sender_email

    print(f"\n{'='*60}")
    print(f"SES Email Authentication Setup")
    print(f"{'='*60}")
    print(f"Domain: {domain}")
    print(f"Region: {args.region}")
    print(f"{'='*60}\n")

    # Initialize AWS clients
    ses_client = boto3.client("ses", region_name=args.region)
    route53_client = boto3.client("route53")

    # Step 1: Check SES sandbox status
    print("Step 1: Checking SES account status...")
    check_ses_sandbox_status(ses_client)
    print()

    # Step 2: Verify domain identity
    print("Step 2: Verifying domain identity in SES...")
    if not verify_domain_identity(ses_client, domain):
        sys.exit(1)
    print()

    # Step 3: Set up DKIM
    print("Step 3: Setting up DKIM...")
    dkim_tokens = setup_dkim(ses_client, domain)
    if not dkim_tokens:
        sys.exit(1)
    print()

    # Step 4: Set up custom MAIL FROM domain
    print("Step 4: Setting up custom MAIL FROM domain...")
    setup_mail_from_domain(ses_client, domain)
    print()

    # Step 5: Create DNS records in Route53
    if not args.skip_dns:
        print("Step 5: Creating DNS records in Route53...")
        hosted_zone_id = get_hosted_zone_id(route53_client, domain)

        if hosted_zone_id:
            print(f"  Found hosted zone: {hosted_zone_id}")
            create_dns_records(
                route53_client,
                hosted_zone_id,
                domain,
                dkim_tokens,
                dmarc_email,
                args.region
            )
        else:
            print(f"⚠ No Route53 hosted zone found for {domain}")
            print("  You'll need to add these DNS records manually:\n")
            print("  SPF Record (TXT):")
            print(f"    Name: {domain}")
            print(f'    Value: "v=spf1 include:amazonses.com ~all"\n')
            print("  DKIM Records (CNAME):")
            for token in dkim_tokens:
                print(f"    Name: {token}._domainkey.{domain}")
                print(f"    Value: {token}.dkim.amazonses.com\n")
            print("  DMARC Record (TXT):")
            print(f"    Name: _dmarc.{domain}")
            print(f'    Value: "v=DMARC1; p=none; rua=mailto:{dmarc_email}"\n')
            print("  MAIL FROM MX Record:")
            print(f"    Name: mail.{domain}")
            print(f"    Value: 10 feedback-smtp.{args.region}.amazonses.com\n")
            print("  MAIL FROM SPF Record (TXT):")
            print(f"    Name: mail.{domain}")
            print(f'    Value: "v=spf1 include:amazonses.com ~all"')
    else:
        print("Step 5: Skipping DNS record creation (--skip-dns flag)")
        print("\n  Add these DNS records manually:\n")
        print("  SPF Record (TXT):")
        print(f"    Name: {domain}")
        print(f'    Value: "v=spf1 include:amazonses.com ~all"\n')
        print("  DKIM Records (CNAME):")
        for token in dkim_tokens:
            print(f"    Name: {token}._domainkey.{domain}")
            print(f"    Value: {token}.dkim.amazonses.com\n")
        print("  DMARC Record (TXT):")
        print(f"    Name: _dmarc.{domain}")
        print(f'    Value: "v=DMARC1; p=none; rua=mailto:{dmarc_email}"\n')
    print()

    # Step 6: Wait for DKIM verification
    if args.wait_for_dkim:
        print("Step 6: Waiting for DKIM verification...")
        check_dkim_status(ses_client, domain)
    else:
        print("Step 6: Skipping DKIM verification wait")
        print("  Run with --wait-for-dkim to wait, or check status in AWS Console")

    print(f"\n{'='*60}")
    print("Setup complete!")
    print(f"{'='*60}")
    print("\nNext steps:")
    print("1. Wait 5-10 minutes for DNS propagation")
    print("2. Verify DKIM status in AWS SES Console")
    print("3. If in sandbox, request production access")
    print("4. Test by sending an email and checking Gmail headers")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
