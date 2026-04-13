"""
Get License Usage Report
Queries Microsoft Graph API for all subscribed SKUs and produces a license
utilization report showing assigned vs available units and estimated monthly cost.

Helps identify over-provisioned subscriptions and wasted licenses.

Requirements:
    pip install msal requests

Authentication:
    Uses app-only authentication with client credentials.
    Register an app in Entra ID with Organization.Read.All permission.

Usage:
    python get_license_usage_report.py --output license_report.csv
    python get_license_usage_report.py --pricing pricing.json --output license_report.csv
"""

import argparse
import csv
import json
import sys

try:
    import msal
    import requests
except ImportError:
    print("Missing dependencies. Run: pip install msal requests")
    sys.exit(1)

# Common M365 SKU friendly names (Microsoft uses internal SKU IDs, not product names)
SKU_NAMES = {
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Standard",
    "SMB_BUSINESS_PREMIUM": "Microsoft 365 Business Premium",
    "EXCHANGESTANDARD": "Exchange Online Plan 1",
    "EXCHANGEENTERPRISE": "Exchange Online Plan 2",
    "FLOW_FREE": "Power Automate Free",
    "POWER_BI_STANDARD": "Power BI Free",
    "POWER_BI_PRO": "Power BI Pro",
    "VISIOCLIENT": "Visio Plan 2",
    "PROJECTPROFESSIONAL": "Project Plan 3",
    "EMS": "Enterprise Mobility + Security E3",
    "EMSPREMIUM": "Enterprise Mobility + Security E5",
    "ATP_ENTERPRISE": "Microsoft Defender for Office 365 Plan 1",
    "THREAT_INTELLIGENCE": "Microsoft Defender for Office 365 Plan 2",
    "WIN_DEF_ATP": "Microsoft Defender for Endpoint Plan 2",
    "AAD_PREMIUM": "Entra ID P1",
    "AAD_PREMIUM_P2": "Entra ID P2",
    "INTUNE_A": "Microsoft Intune Plan 1",
}


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


def get_subscribed_skus(token):
    """Fetch all subscribed SKUs from the tenant."""
    url = "https://graph.microsoft.com/v1.0/subscribedSkus"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"API error {response.status_code}: {response.text[:200]}")
        sys.exit(1)
    return response.json().get("value", [])


def main():
    parser = argparse.ArgumentParser(description="Report Microsoft 365 license utilization")
    parser.add_argument("--tenant-id", required=True, help="Azure AD tenant ID")
    parser.add_argument("--client-id", required=True, help="App registration client ID")
    parser.add_argument("--client-secret", required=True, help="App registration client secret")
    parser.add_argument("--output", default="license_report.csv", help="Output CSV path")
    parser.add_argument("--pricing", help="Optional JSON file mapping SKU part numbers to monthly USD price per unit")
    args = parser.parse_args()

    pricing = {}
    if args.pricing:
        with open(args.pricing, "r") as f:
            pricing = json.load(f)

    print(f"Authenticating to tenant {args.tenant_id}...")
    token = get_access_token(args.tenant_id, args.client_id, args.client_secret)

    print("Fetching license subscriptions...")
    skus = get_subscribed_skus(token)

    rows = []
    total_waste = 0.0

    for sku in skus:
        part_number = sku.get("skuPartNumber", "Unknown")
        friendly_name = SKU_NAMES.get(part_number, part_number)
        total = sku.get("prepaidUnits", {}).get("enabled", 0)
        consumed = sku.get("consumedUnits", 0)
        available = total - consumed
        utilization = (consumed / total * 100) if total > 0 else 0

        unit_price = pricing.get(part_number, 0)
        monthly_waste = available * unit_price

        if unit_price > 0:
            total_waste += monthly_waste

        rows.append({
            "SKU": part_number,
            "Product": friendly_name,
            "Total": total,
            "Assigned": consumed,
            "Available": available,
            "Utilization": f"{utilization:.1f}%",
            "UnitPrice_USD": f"${unit_price:.2f}" if unit_price else "",
            "MonthlyWaste_USD": f"${monthly_waste:.2f}" if unit_price else "",
        })

    rows.sort(key=lambda x: x["Assigned"], reverse=True)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nFound {len(rows)} license subscriptions.")
    print(f"Report saved to: {args.output}")

    under_utilized = [r for r in rows if float(r["Utilization"].rstrip("%")) < 50 and r["Total"] > 5]
    if under_utilized:
        print(f"\nUnder-utilized licenses (<50% assigned, >5 total):")
        for r in under_utilized:
            print(f"  {r['Product']}: {r['Assigned']}/{r['Total']} assigned ({r['Utilization']})")

    if total_waste > 0:
        print(f"\nEstimated monthly waste from unassigned licenses: ${total_waste:.2f}")


if __name__ == "__main__":
    main()
