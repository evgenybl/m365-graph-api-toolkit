"""
Get Inactive Users Report
Queries Microsoft Graph API for users who haven't signed in within a specified number of days.
Outputs a CSV report with user details, last sign-in date, and assigned licenses.

Useful for license reclamation, security audits, and compliance reviews.

Requirements:
    pip install msal requests

Authentication:
    Uses app-only authentication with client credentials.
    Register an app in Entra ID with User.Read.All and AuditLog.Read.All permissions.

Usage:
    python get_inactive_users_report.py --days 90 --output inactive_users.csv
    python get_inactive_users_report.py --days 60 --licensed-only
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone

try:
    import msal
    import requests
except ImportError:
    print("Missing dependencies. Run: pip install msal requests")
    sys.exit(1)


def get_access_token(tenant_id, client_id, client_secret):
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        print(f"Authentication failed: {result.get('error_description', 'Unknown error')}")
        sys.exit(1)
    return result["access_token"]


def get_all_users(token):
    """Fetch all users with sign-in activity and license details."""
    url = "https://graph.microsoft.com/v1.0/users"
    params = {
        "$select": "displayName,userPrincipalName,mail,accountEnabled,"
                   "signInActivity,assignedLicenses,department,jobTitle,createdDateTime",
        "$top": "999",
    }
    headers = {"Authorization": f"Bearer {token}"}
    users = []

    while url:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"API error {response.status_code}: {response.text[:200]}")
            sys.exit(1)
        data = response.json()
        users.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None  # nextLink already includes params

    return users


def parse_sign_in_date(user):
    """Extract last sign-in date from user object. Returns None if never signed in."""
    activity = user.get("signInActivity")
    if not activity:
        return None
    last_sign_in = activity.get("lastSignInDateTime")
    if not last_sign_in:
        return None
    return datetime.fromisoformat(last_sign_in.replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser(description="Report inactive Microsoft 365 users")
    parser.add_argument("--tenant-id", required=True, help="Azure AD tenant ID")
    parser.add_argument("--client-id", required=True, help="App registration client ID")
    parser.add_argument("--client-secret", required=True, help="App registration client secret")
    parser.add_argument("--days", type=int, default=90, help="Days of inactivity threshold (default: 90)")
    parser.add_argument("--output", default="inactive_users.csv", help="Output CSV path")
    parser.add_argument("--licensed-only", action="store_true", help="Only report users with licenses assigned")
    args = parser.parse_args()

    print(f"Authenticating to tenant {args.tenant_id}...")
    token = get_access_token(args.tenant_id, args.client_id, args.client_secret)

    print("Fetching all users (this may take a minute for large tenants)...")
    users = get_all_users(token)
    print(f"Found {len(users)} total users.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    inactive = []

    for user in users:
        if not user.get("accountEnabled", False):
            continue

        last_sign_in = parse_sign_in_date(user)
        has_licenses = len(user.get("assignedLicenses", [])) > 0

        if args.licensed_only and not has_licenses:
            continue

        is_inactive = last_sign_in is None or last_sign_in < cutoff

        if is_inactive:
            days_since = "Never" if last_sign_in is None else (datetime.now(timezone.utc) - last_sign_in).days
            inactive.append({
                "DisplayName": user.get("displayName", ""),
                "UserPrincipalName": user.get("userPrincipalName", ""),
                "Email": user.get("mail", ""),
                "Department": user.get("department", ""),
                "JobTitle": user.get("jobTitle", ""),
                "AccountEnabled": user.get("accountEnabled", ""),
                "LastSignIn": last_sign_in.strftime("%Y-%m-%d %H:%M") if last_sign_in else "Never",
                "DaysInactive": days_since,
                "LicenseCount": len(user.get("assignedLicenses", [])),
                "CreatedDate": user.get("createdDateTime", "")[:10],
            })

    inactive.sort(key=lambda x: str(x["DaysInactive"]), reverse=True)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=inactive[0].keys() if inactive else [])
        writer.writeheader()
        writer.writerows(inactive)

    print(f"\nResults: {len(inactive)} inactive users (>{args.days} days) out of {len(users)} total.")
    print(f"Report saved to: {args.output}")

    licensed_inactive = sum(1 for u in inactive if u["LicenseCount"] > 0)
    if licensed_inactive:
        print(f"License reclamation candidates: {licensed_inactive} inactive users with active licenses.")


if __name__ == "__main__":
    main()
