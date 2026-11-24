# Changelog

## 2026-11-24

- `get_security_alerts_report.py`: the header names `SecurityAlert.Read.All`, the permission for the `alerts_v2` endpoint it calls, instead of `SecurityEvents.Read.All`.
- `get_security_alerts_report.py`: the header no longer says it reads only active alerts; it reads alerts of any status created in the look-back period.
- `get_inactive_users_report.py`: rows are sorted by DaysInactive as a number (never signed in first, then most days first), not as text.
- `get_inactive_users_report.py`: a user whose `createdDateTime` is null no longer stops the script with an error.
- `get_license_usage_report.py`: follows `@odata.nextLink` like the other two scripts, so a paged answer is read to the last page.
- `get_license_usage_report.py`: a SKU with more units assigned than enabled counts as zero waste instead of lowering the estimated total.
- All three scripts: the CSV header row is written even when the report has no rows.
- All three scripts: the usage examples in each header say that `--tenant-id`, `--client-id` and `--client-secret` are required.
- Added `requirements.txt` (msal, requests; no version pinned).
- Added standard-library tests that run the scripts against an in-memory fake of Microsoft Graph, and a GitHub Actions workflow that runs them.
- README: install from `requirements.txt`, complete example commands, output columns for every script, how to run the tests, and updated known limitations.
