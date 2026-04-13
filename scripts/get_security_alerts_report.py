"""
Get Security Alerts Report
Queries Microsoft Graph Security API for active alerts and produces a
prioritized report grouped by severity.

Covers alerts from Defender for Endpoint, Defender for Identity, Defender
for Office 365, Entra ID Protection, and other integrated security providers.

Requirements:
    pip install msal requests

Authentication:
    Uses app-only authentication with client credentials.
    Register an app in Entra ID with SecurityEvents.Read.All permission.

Usage:
    python get_security_alerts_report.py --output alerts.csv
    python get_security_alerts_report.py --severity high --days 7
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


def get_alerts(token, severity_filter=None, days=30):
    """Fetch security alerts from Microsoft Graph Security API."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    url = "https://graph.microsoft.com/v1.0/security/alerts_v2"
    params = {
        "$top": "999",
        "$orderby": "createdDateTime desc",
        "$filter": f"createdDateTime ge {cutoff}",
    }

    if severity_filter:
        params["$filter"] += f" and severity eq '{severity_filter}'"

    headers = {"Authorization": f"Bearer {token}"}
    alerts = []

    while url:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"API error {response.status_code}: {response.text[:200]}")
            sys.exit(1)
        data = response.json()
        alerts.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None

    return alerts


def extract_affected_entities(alert):
    """Pull affected user or device from alert evidence."""
    evidence = alert.get("evidence", [])
    entities = []
    for item in evidence:
        entity_type = item.get("@odata.type", "")
        if "user" in entity_type.lower():
            upn = item.get("userAccount", {}).get("userPrincipalName", "")
            if upn:
                entities.append(f"User: {upn}")
        elif "device" in entity_type.lower():
            name = item.get("deviceDnsName", "")
            if name:
                entities.append(f"Device: {name}")
    return "; ".join(entities[:3]) if entities else ""


def main():
    parser = argparse.ArgumentParser(description="Report Microsoft 365 security alerts")
    parser.add_argument("--tenant-id", required=True, help="Azure AD tenant ID")
    parser.add_argument("--client-id", required=True, help="App registration client ID")
    parser.add_argument("--client-secret", required=True, help="App registration client secret")
    parser.add_argument("--output", default="security_alerts.csv", help="Output CSV path")
    parser.add_argument("--severity", choices=["high", "medium", "low", "informational"],
                        help="Filter by severity level")
    parser.add_argument("--days", type=int, default=30, help="Look back period in days (default: 30)")
    args = parser.parse_args()

    print(f"Authenticating to tenant {args.tenant_id}...")
    token = get_access_token(args.tenant_id, args.client_id, args.client_secret)

    severity_label = args.severity or "all"
    print(f"Fetching {severity_label} security alerts from last {args.days} days...")
    alerts = get_alerts(token, args.severity, args.days)
    print(f"Found {len(alerts)} alerts.")

    rows = []
    severity_counts = {"high": 0, "medium": 0, "low": 0, "informational": 0}

    for alert in alerts:
        severity = alert.get("severity", "unknown").lower()
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

        created = alert.get("createdDateTime", "")[:19].replace("T", " ")
        status = alert.get("status", "")
        classification = alert.get("classification", "")

        rows.append({
            "Severity": severity.capitalize(),
            "Title": alert.get("title", ""),
            "Status": status,
            "Classification": classification,
            "Category": alert.get("category", ""),
            "Source": alert.get("serviceSource", ""),
            "Created": created,
            "AffectedEntities": extract_affected_entities(alert),
            "AlertId": alert.get("id", ""),
        })

    # Sort: high first, then by date
    severity_order = {"High": 0, "Medium": 1, "Low": 2, "Informational": 3}
    rows.sort(key=lambda x: (severity_order.get(x["Severity"], 9), x["Created"]))

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nReport saved to: {args.output}")
    print(f"\nBreakdown by severity:")
    for level in ["high", "medium", "low", "informational"]:
        count = severity_counts.get(level, 0)
        if count > 0:
            print(f"  {level.capitalize()}: {count}")

    high_new = sum(1 for r in rows if r["Severity"] == "High" and r["Status"] == "new")
    if high_new > 0:
        print(f"\n** {high_new} HIGH severity alerts still in 'new' status -- needs attention **")


if __name__ == "__main__":
    main()
