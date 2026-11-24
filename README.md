# M365 Graph API Toolkit

[![tests](https://github.com/evgenybl/m365-graph-api-toolkit/actions/workflows/tests.yml/badge.svg)](https://github.com/evgenybl/m365-graph-api-toolkit/actions/workflows/tests.yml)

Python scripts for Microsoft 365 administration via Microsoft Graph API. Built for IT admins who want quick, reliable reports without navigating the admin center.

These scripts handle authentication, pagination, and CSV export.

**Status: reference code.** The tests in `tests/` run the three scripts against an in-memory fake of Microsoft Graph with invented data, on every push to `main` and every pull request. The scripts have not been run against a live tenant from this repository. Try them on a test tenant first.

---

## Scripts

| Script | What it does |
|---|---|
| [get_inactive_users_report.py](./scripts/get_inactive_users_report.py) | Finds users who haven't signed in within N days. Counts the licensed ones as license reclamation candidates. |
| [get_license_usage_report.py](./scripts/get_license_usage_report.py) | Full license utilization report: assigned vs available units, under-utilized SKUs, estimated monthly waste. |
| [get_security_alerts_report.py](./scripts/get_security_alerts_report.py) | Pulls security alerts from Defender, Identity Protection, and other providers. Prioritized by severity. |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` lists the two packages the scripts import, `msal` and `requests`. No version is pinned.

### 2. Register an app in Entra ID

1. Go to **Entra ID > App registrations > New registration**
2. Name it something like "M365 Reporting Scripts"
3. Set it to single tenant
4. Create a client secret under **Certificates & secrets**
5. Grant these **application permissions** under **API permissions**:
   - `User.Read.All` (for user reports)
   - `AuditLog.Read.All` (for sign-in activity; Microsoft Graph also requires a Microsoft Entra ID P1 or P2 license for it)
   - `Organization.Read.All` (for license reports)
   - `SecurityAlert.Read.All` (for security alerts: the permission Microsoft documents for `alerts_v2`, which the script calls)
6. Click **Grant admin consent**

### 3. Run a script

```bash
python scripts/get_inactive_users_report.py \
    --tenant-id <tenant-id> \
    --client-id <client-id> \
    --client-secret <client-secret> \
    --days 90 \
    --output inactive_users.csv
```

Every script requires `--tenant-id`, `--client-id` and `--client-secret`. Replace each `<...>` placeholder with your own value.

---

## Script Details

### get_inactive_users_report.py

Finds enabled users with no sign-in activity within a configurable number of days.

```bash
# Users inactive for 90+ days
python scripts/get_inactive_users_report.py --tenant-id <tenant-id> --client-id <client-id> --client-secret <client-secret> --days 90 --output inactive.csv

# Only licensed users (license reclamation candidates)
python scripts/get_inactive_users_report.py --tenant-id <tenant-id> --client-id <client-id> --client-secret <client-secret> --days 60 --licensed-only
```

**Output columns:** DisplayName, UserPrincipalName, Email, Department, JobTitle, AccountEnabled, LastSignIn, DaysInactive, LicenseCount, CreatedDate

Rows are sorted by DaysInactive, most inactive first: users who have never signed in (`Never`) come first, then the rest by number of days.

---

### get_license_usage_report.py

Reports on every subscribed SKU in the tenant: how many license units are enabled, how many are assigned, and how many are sitting unused.

```bash
# Basic report
python scripts/get_license_usage_report.py --tenant-id <tenant-id> --client-id <client-id> --client-secret <client-secret> --output licenses.csv

# With pricing data for waste calculation
python scripts/get_license_usage_report.py --tenant-id <tenant-id> --client-id <client-id> --client-secret <client-secret> --pricing pricing.json --output licenses.csv
```

**Output columns:** SKU, Product, Total, Assigned, Available, Utilization, UnitPrice_USD, MonthlyWaste_USD

Total is the SKU's `prepaidUnits.enabled`, Assigned is its `consumedUnits`, and Available is Total minus Assigned (negative when more units are assigned than enabled). Product is a friendly name from a short list in the script, or the SKU part number when the list has none. With a pricing file, MonthlyWaste_USD is Available times the unit price, and zero when Available is negative; for a SKU the file does not list, both price columns stay empty. Rows are sorted by Assigned, highest first. SKUs under 50% assigned with more than 5 units, and the estimated total monthly waste, are printed to the console.

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

Pulls the security alerts created in the look-back period (default 30 days), whatever their status, from the Microsoft Graph security API (`alerts_v2`).

```bash
# All alerts from last 30 days
python scripts/get_security_alerts_report.py --tenant-id <tenant-id> --client-id <client-id> --client-secret <client-secret> --output alerts.csv

# Only high severity, last 7 days
python scripts/get_security_alerts_report.py --tenant-id <tenant-id> --client-id <client-id> --client-secret <client-secret> --severity high --days 7
```

`--severity` takes `high`, `medium`, `low` or `informational`. The severity and the look-back period are sent to Graph as a `$filter`.

**Output columns:** Severity, Title, Status, Classification, Category, Source, Created, AffectedEntities, AlertId

Rows are sorted by severity (High, Medium, Low, Informational, then any other value), and within a severity by Created, oldest first. AffectedEntities lists up to three users (by user principal name) or devices (by DNS name) from the alert's evidence. The count for each of the four named severities, and the number of High alerts still in `new` status, are printed to the console.

**Covers:** Defender for Endpoint, Defender for Identity, Defender for Office 365, Entra ID Protection, and any other provider sending alerts through Microsoft Graph Security.

---

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The tests use only the Python standard library. They replace `msal` and `requests` with stubs and run each script against an in-memory fake of Microsoft Graph with invented data: several pages, HTTP errors, throttling, a refused token and missing fields. Nothing is sent to Microsoft. GitHub Actions runs them on Python 3.13 on every push to `main` and every pull request (`.github/workflows/tests.yml`).

---

## Notes

- All scripts follow Microsoft Graph's paging links until the last page.
- Written with AI-assisted workflows (Claude, GitHub Copilot).
- CSV output works with Excel, Power BI, or any reporting tool. The CSV is written as UTF-8, with a header row even when the report has no rows.

---

## Known limitations

- The client secret is passed on the command line, so other users of the machine may see it in the process list, and your shell may keep it in its history. The scripts take it from no other source (no environment variable, secrets manager or certificate); adapt them before regular use.
- No retry: a throttling answer (HTTP 429) or any other HTTP error stops the script with the status code and the first 200 characters of Graph's answer, and no CSV is written.
- Graph requests have no timeout, so a stalled connection can keep a script waiting indefinitely. A network failure ends the script with a Python traceback rather than a short message.
- `get_inactive_users_report.py` reads only `signInActivity.lastSignInDateTime`; no other sign-in field is considered. Disabled accounts are always left out.
- `get_license_usage_report.py` takes Total from `prepaidUnits.enabled` only; the other counts in `prepaidUnits` are not read. Prices come only from the pricing file and are reported as USD.
- `get_security_alerts_report.py` keeps at most three entities per alert, and only user and device evidence.
- The tests run on Python 3.13 only; other Python versions are not tested.

---

## Author

**Evgeny Blekhman** · [LinkedIn](https://www.linkedin.com/in/evgeny-blekhman/) · [GitHub](https://github.com/evgenybl)

AI & Automation Specialist. Practical AI training, workshops, business automation and integrations.
