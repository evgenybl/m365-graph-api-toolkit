# M365 Graph API Toolkit

Python scripts for Microsoft 365 administration via Microsoft Graph API. Built for IT admins who want quick, reliable reports without navigating the admin center.

These scripts handle authentication, pagination, and CSV export.

**Status: reference code.** There are no automated tests in this repository, and the scripts have not been run against a live tenant from here. Try them on a test tenant first.

---

## Scripts

| Script | What it does |
|---|---|
| [get_inactive_users_report.py](./scripts/get_inactive_users_report.py) | Finds users who haven't signed in within N days. Flags licensed inactive accounts for reclamation. |
| [get_license_usage_report.py](./scripts/get_license_usage_report.py) | Full license utilization report: assigned vs available units, under-utilized SKUs, estimated monthly waste. |
| [get_security_alerts_report.py](./scripts/get_security_alerts_report.py) | Pulls security alerts from Defender, Identity Protection, and other providers. Prioritized by severity. |

---

## Setup

### 1. Install dependencies

```bash
pip install msal requests
```

### 2. Register an app in Entra ID

1. Go to **Entra ID > App registrations > New registration**
2. Name it something like "M365 Reporting Scripts"
3. Set it to single tenant
4. Create a client secret under **Certificates & secrets**
5. Grant these **application permissions** under **API permissions**:
   - `User.Read.All` (for user reports)
   - `AuditLog.Read.All` (for sign-in activity; Microsoft Graph also requires a Microsoft Entra ID P1 or P2 license for it)
   - `Organization.Read.All` (for license reports)
   - `SecurityAlert.Read.All` (for security alerts: the permission Microsoft documents for `alerts_v2`, which the script calls; the script's own header still names `SecurityEvents.Read.All`)
6. Click **Grant admin consent**

### 3. Run a script

```bash
python scripts/get_inactive_users_report.py \
    --tenant-id YOUR_TENANT_ID \
    --client-id YOUR_CLIENT_ID \
    --client-secret YOUR_CLIENT_SECRET \
    --days 90 \
    --output inactive_users.csv
```

---

## Script Details

Every command below also needs `--tenant-id`, `--client-id` and `--client-secret`, as in the Setup example. They are left out here to keep the examples short.

### get_inactive_users_report.py

Finds enabled users with no sign-in activity within a configurable number of days.

```bash
# Users inactive for 90+ days
python scripts/get_inactive_users_report.py --days 90 --output inactive.csv

# Only licensed users (license reclamation candidates)
python scripts/get_inactive_users_report.py --days 60 --licensed-only
```

**Output columns:** DisplayName, UserPrincipalName, Email, Department, JobTitle, AccountEnabled, LastSignIn, DaysInactive, LicenseCount, CreatedDate

---

### get_license_usage_report.py

Reports on every subscribed SKU in the tenant: how many licenses are purchased, how many are assigned, and how many are sitting unused.

```bash
# Basic report
python scripts/get_license_usage_report.py --output licenses.csv

# With pricing data for waste calculation
python scripts/get_license_usage_report.py --pricing pricing.json --output licenses.csv
```

**Pricing file format** (optional):
```json
{
    "SPE_E3": 36.00,
    "ENTERPRISEPACK": 23.00,
    "AAD_PREMIUM_P2": 9.00
}
```

---

### get_security_alerts_report.py

Pulls active security alerts from all integrated Microsoft security services.

```bash
# All alerts from last 30 days
python scripts/get_security_alerts_report.py --output alerts.csv

# Only high severity, last 7 days
python scripts/get_security_alerts_report.py --severity high --days 7
```

**Covers:** Defender for Endpoint, Defender for Identity, Defender for Office 365, Entra ID Protection, and any other provider sending alerts through Microsoft Graph Security.

---

## Notes

- All scripts follow Microsoft Graph's paging links until the last page.
- Credentials are passed as command-line arguments. For production use, switch to environment variables or a secrets manager.
- Written with AI-assisted workflows (Claude, GitHub Copilot).
- Known limitation: `get_inactive_users_report.py` sorts its CSV by days inactive as text, so the order is not numeric.
- CSV output works with Excel, Power BI, or any reporting tool.

---

## Author

**Evgeny Blekhman** · [LinkedIn](https://www.linkedin.com/in/evgeny-blekhman/) · [GitHub](https://github.com/evgenybl)
