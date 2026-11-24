"""Tests for the three report scripts.

The scripts run against an in-memory fake of Microsoft Graph (tests/fake_graph.py)
with invented data. Standard library only: stub modules replace msal and requests
before the scripts are imported, so neither package has to be installed, and no
test opens a network connection.

Run from the repository root:

    python -m unittest discover -s tests -v
"""

import contextlib
import csv
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(TESTS_DIR)
SCRIPTS_DIR = os.path.join(REPO_DIR, "scripts")
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import fake_graph  # noqa: E402  (needs TESTS_DIR on sys.path)

# The stubs go in before any script is imported; the real modules, if any, come back afterwards.
_SAVED_MODULES = {name: sys.modules.get(name) for name in ("msal", "requests")}
fake_graph.install()


def load_script(file_name):
    """Import scripts/<file_name> as a module without running its main()."""
    path = os.path.join(SCRIPTS_DIR, file_name)
    spec = importlib.util.spec_from_file_location("m365_toolkit_" + file_name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INACTIVE_USERS = "get_inactive_users_report.py"
LICENSE_USAGE = "get_license_usage_report.py"
SECURITY_ALERTS = "get_security_alerts_report.py"

inactive_users = load_script(INACTIVE_USERS)
license_usage = load_script(LICENSE_USAGE)
security_alerts = load_script(SECURITY_ALERTS)


def tearDownModule():
    for name, module in _SAVED_MODULES.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


# Invented identifiers. The secret-shaped values are built at runtime so that no
# literal in this file looks like a real credential.
TENANT_ID = "00000000-0000-0000-0000-000000000001"
CLIENT_ID = "00000000-0000-0000-0000-000000000002"
CLIENT_SECRET = "fake" + "-" + "client" + "-" + "secret"
ACCESS_TOKEN = "fake" + "-" + "access" + "-" + "token"


def documented_columns(script_name):
    """The column list README.md documents for a script, in order."""
    with open(os.path.join(REPO_DIR, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    section = readme.split("### " + script_name, 1)[1]
    section = re.split(r"\n#{2,3} ", section, maxsplit=1)[0]
    match = re.search(r"\*\*Output columns:\*\*\s*(.+)", section)
    if match is None:
        raise AssertionError("README.md documents no output columns for " + script_name)
    return [column.strip() for column in match.group(1).split(",")]


def run_as_program(scenario, script_name, output, cwd):
    """Run a script as __main__ in a child Python, against the fake (see fake_graph.py)."""
    command = [
        sys.executable, "-I", "-B",
        os.path.join(TESTS_DIR, "fake_graph.py"), scenario,
        os.path.join(SCRIPTS_DIR, script_name),
        "--tenant-id", TENANT_ID,
        "--client-id", CLIENT_ID,
        "--client-secret", CLIENT_SECRET,
        "--output", output,
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=120, cwd=cwd)


class ReportTestCase(unittest.TestCase):
    """Runs one script's main() in this process against a fresh fake Graph."""

    script = None  # the imported script module
    script_name = None  # its file name in scripts/
    graph_path = None  # the Graph collection it reads

    def setUp(self):
        self.graph = fake_graph.reset(ACCESS_TOKEN)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.output = os.path.join(self.tmp, "report.csv")

    def _argv(self, extra_args):
        return [
            self.script_name,
            "--tenant-id", TENANT_ID,
            "--client-id", CLIENT_ID,
            "--client-secret", CLIENT_SECRET,
            "--output", self.output,
            *extra_args,
        ]

    def run_script(self, *extra_args):
        """Run main() to completion; return what it printed."""
        console = io.StringIO()
        with mock.patch.object(sys, "argv", self._argv(extra_args)), contextlib.redirect_stdout(console):
            self.script.main()
        printed = console.getvalue()
        self.assert_no_credentials(printed)
        return printed

    def run_script_expecting_exit(self, *extra_args):
        """Run main() expecting sys.exit; return the exit code and what it printed."""
        console = io.StringIO()
        with mock.patch.object(sys, "argv", self._argv(extra_args)), contextlib.redirect_stdout(console):
            with self.assertRaises(SystemExit) as caught:
                self.script.main()
        printed = console.getvalue()
        self.assert_no_credentials(printed)
        return caught.exception.code, printed

    def read_csv(self):
        """Return the CSV's header and its rows as dicts; check it carries no credential."""
        with open(self.output, newline="", encoding="utf-8") as f:
            text = f.read()
        self.assert_no_credentials(text)
        records = list(csv.reader(io.StringIO(text, newline="")))
        header = records[0]
        return header, [dict(zip(header, record)) for record in records[1:]]

    def assert_no_credentials(self, text):
        self.assertNotIn(CLIENT_SECRET, text)
        self.assertNotIn(ACCESS_TOKEN, text)

    def assert_every_request_authorized(self):
        self.assertTrue(self.graph.requests)
        for request in self.graph.requests:
            self.assertEqual(request["headers"].get("Authorization"), "Bearer " + ACCESS_TOKEN)

    def assert_one_app_only_token_request(self):
        self.assertEqual(fake_graph.STATE.apps, [{
            "client_id": CLIENT_ID,
            "authority": "https://login.microsoftonline.com/" + TENANT_ID,
            "client_credential": CLIENT_SECRET,
        }])
        self.assertEqual(fake_graph.STATE.scopes, [["https://graph.microsoft.com/.default"]])

    def assert_paged_requests(self, expected_count):
        """The first request names the collection; each later one follows a nextLink as given."""
        self.assertEqual(len(self.graph.requests), expected_count)
        first, *later = self.graph.requests
        self.assertEqual(first["url"], fake_graph.GRAPH_ROOT + self.graph_path)
        for number, request in enumerate(later, start=1):
            self.assertEqual(
                request["url"],
                f"{fake_graph.GRAPH_ROOT}{self.graph_path}?{fake_graph.SKIPTOKEN}=page-{number}",
            )
            self.assertIsNone(request["params"], "a nextLink already carries the query")


class CommonFailureTests:
    """Paths every script shares. Mixed into each script's test class."""

    def seed_one_page(self):
        raise NotImplementedError

    def seed_four_pages(self):
        raise NotImplementedError

    def test_token_failure_exits_cleanly_without_the_secret(self):
        fake_graph.fail_token("Invented error for the tests: the client secret was rejected.")
        code, printed = self.run_script_expecting_exit()
        self.assertEqual(code, 1)
        self.assertIn("Authentication failed: Invented error for the tests", printed)
        self.assertEqual(self.graph.requests, [])
        self.assertFalse(os.path.exists(self.output))

    def test_http_error_exits_cleanly_without_a_report(self):
        self.seed_one_page()
        self.graph.fail(self.graph_path, 403, fake_graph.graph_error(
            "Authorization_RequestDenied", "Insufficient privileges to complete the operation."))
        code, printed = self.run_script_expecting_exit()
        self.assertEqual(code, 1)
        self.assertIn("API error 403", printed)
        self.assertIn("Authorization_RequestDenied", printed)
        self.assertFalse(os.path.exists(self.output))

    def test_http_error_on_a_later_page_writes_no_partial_report(self):
        self.seed_four_pages()
        self.graph.fail(self.graph_path, 500, fake_graph.graph_error("InternalServerError", "Invented failure."), page=2)
        code, printed = self.run_script_expecting_exit()
        self.assertEqual(code, 1)
        self.assertIn("API error 500", printed)
        self.assertEqual(len(self.graph.requests), 3)
        self.assertFalse(os.path.exists(self.output))

    def test_throttling_429_is_reported_and_not_retried(self):
        self.seed_one_page()
        self.graph.fail(self.graph_path, 429, fake_graph.graph_error("TooManyRequests", "Invented throttling."),
                        headers={"Retry-After": "10"})
        code, printed = self.run_script_expecting_exit()
        self.assertEqual(code, 1)
        self.assertIn("API error 429", printed)
        self.assertEqual(len(self.graph.requests), 1)
        self.assertFalse(os.path.exists(self.output))

    def test_empty_result_still_writes_the_documented_header(self):
        self.graph.add_collection(self.graph_path, [], page_size=10)
        self.run_script()
        header, rows = self.read_csv()
        self.assertEqual(header, documented_columns(self.script_name))
        self.assertEqual(rows, [])

    def test_as_a_program_token_failure_exits_1_without_traceback(self):
        result = run_as_program("token-failure", self.script_name, self.output, self.tmp)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("Authentication failed", result.stdout)
        self.assertNotIn(CLIENT_SECRET, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.output))

    def test_as_a_program_empty_tenant_exits_0_with_header(self):
        result = run_as_program("empty", self.script_name, self.output, self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn(CLIENT_SECRET, result.stdout)
        header, rows = self.read_csv()
        self.assertEqual(header, documented_columns(self.script_name))
        self.assertEqual(rows, [])


# ---------------------------------------------------------------- inactive users

def iso_days_ago(days):
    """A Graph-style UTC timestamp days (plus one hour) before now."""
    moment = datetime.now(timezone.utc) - timedelta(days=days, hours=1)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_user(number, days_since_sign_in, **overrides):
    """An invented enabled, licensed user at contoso.com. None: never signed in."""
    last = iso_days_ago(days_since_sign_in) if days_since_sign_in is not None else None
    user = {
        "displayName": f"Contoso User {number}",
        "userPrincipalName": f"user{number}@contoso.com",
        "mail": f"user{number}@contoso.com",
        "accountEnabled": True,
        "signInActivity": {"lastSignInDateTime": last},
        "assignedLicenses": [{"skuId": "00000000-0000-0000-0000-0000000000a1", "disabledPlans": []}],
        "department": "Finance",
        "jobTitle": "Analyst",
        "createdDateTime": "2024-03-01T09:00:00Z",
    }
    user.update(overrides)
    return user


class TestInactiveUsersReport(CommonFailureTests, ReportTestCase):
    script = inactive_users
    script_name = INACTIVE_USERS
    graph_path = "/users"

    def seed_one_page(self):
        self.graph.add_collection(self.graph_path, [make_user(1, 200)], page_size=10)

    def seed_four_pages(self):
        users = [make_user(n, 200) for n in range(1, 9)]
        self.graph.add_collection(self.graph_path, users, page_size=2)

    def test_reads_every_page_and_reports_each_inactive_user_once(self):
        users = [
            make_user(1, 120),
            make_user(2, None),
            make_user(3, 45),
            make_user(4, 5),  # active
            make_user(5, 400, accountEnabled=False),  # disabled: never reported
            make_user(6, 31),
            make_user(7, 29),  # active
            make_user(8, 90, assignedLicenses=[]),
        ]
        self.graph.add_collection(self.graph_path, users, page_size=3)

        printed = self.run_script("--days", "30")

        header, rows = self.read_csv()
        self.assertEqual(header, documented_columns(self.script_name))
        upns =[row["UserPrincipalName"] for row in rows]
        self.assertEqual(sorted(upns), [f"user{n}@contoso.com" for n in (1, 2, 3, 6, 8)])
        self.assertEqual(len(upns), len(set(upns)))

        first_user = next(row for row in rows if row["UserPrincipalName"] == "user1@contoso.com")
        self.assertEqual(first_user["DisplayName"], "Contoso User 1")
        self.assertEqual(first_user["Email"], "user1@contoso.com")
        self.assertEqual(first_user["Department"], "Finance")
        self.assertEqual(first_user["JobTitle"], "Analyst")
        self.assertEqual(first_user["AccountEnabled"], "True")
        self.assertRegex(first_user["LastSignIn"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(first_user["DaysInactive"], "120")
        self.assertEqual(first_user["LicenseCount"], "1")
        self.assertEqual(first_user["CreatedDate"], "2024-03-01")

        self.assert_paged_requests(3)
        first_params = self.graph.requests[0]["params"]
        self.assertIn("signInActivity", first_params["$select"])
        self.assertEqual(first_params["$top"], "999")
        self.assert_every_request_authorized()
        self.assert_one_app_only_token_request()
        self.assertIn("Found 8 total users.", printed)
        self.assertIn("Results: 5 inactive users (>30 days) out of 8 total.", printed)
        self.assertIn("License reclamation candidates: 4 inactive users with active licenses.", printed)
        self.assertEqual(header, inactive_users.COLUMNS)

    def test_sorts_days_inactive_as_numbers_never_signed_in_first(self):
        # Control: a text sort puts "100" after "15" and "20" (it would give Never, 365, 20, 15, 100).
        users = [
            make_user(1, 100),
            make_user(2, 20),
            make_user(3, None),
            make_user(4, 365),
            make_user(5, 15),
            make_user(6, 9),  # active with --days 10
        ]
        self.graph.add_collection(self.graph_path, users, page_size=2)

        self.run_script("--days", "10")

        _, rows = self.read_csv()
        self.assertEqual([row["DaysInactive"] for row in rows], ["Never", "365", "100", "20", "15"])
        self.assertEqual(rows[0]["LastSignIn"], "Never")

    def test_missing_and_null_optional_fields_do_not_break_the_report(self):
        sparse = {"userPrincipalName": "sparse@example.com", "accountEnabled": True}
        nulls = {
            "displayName": None,
            "userPrincipalName": "nulls@example.com",
            "mail": None,
            "accountEnabled": True,
            "signInActivity": None,
            "assignedLicenses": [],
            "department": None,
            "jobTitle": None,
            "createdDateTime": None,
        }
        self.graph.add_collection(self.graph_path, [sparse, nulls], page_size=10)

        self.run_script()

        _, rows = self.read_csv()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["DisplayName"], "")
            self.assertEqual(row["Email"], "")
            self.assertEqual(row["Department"], "")
            self.assertEqual(row["JobTitle"], "")
            self.assertEqual(row["LastSignIn"], "Never")
            self.assertEqual(row["DaysInactive"], "Never")
            self.assertEqual(row["LicenseCount"], "0")
            self.assertEqual(row["CreatedDate"], "")

    def test_licensed_only_leaves_out_users_without_licenses(self):
        users = [make_user(1, 200), make_user(2, 200, assignedLicenses=[]), make_user(3, None)]
        self.graph.add_collection(self.graph_path, users, page_size=10)

        self.run_script("--licensed-only")

        _, rows = self.read_csv()
        self.assertEqual(sorted(row["UserPrincipalName"] for row in rows),
                         ["user1@contoso.com", "user3@contoso.com"])

    def test_writes_non_ascii_names_as_utf8(self):
        # Built with chr() so that this file stays ASCII; U+0141 is not in Windows-1252.
        name = "Zo" + chr(0xEB) + " " + chr(0x141) + "ow"
        self.graph.add_collection(self.graph_path, [make_user(1, 200, displayName=name)], page_size=10)

        self.run_script()

        _, rows = self.read_csv()
        self.assertEqual(rows[0]["DisplayName"], name)


# ---------------------------------------------------------------- license usage

def make_sku(number, part_number, enabled, consumed):
    """An invented subscribed SKU."""
    return {
        "skuId": f"00000000-0000-0000-0000-0000000000b{number}",
        "skuPartNumber": part_number,
        "consumedUnits": consumed,
        "prepaidUnits": {"enabled": enabled, "suspended": 0, "warning": 0, "lockedOut": 0},
        "capabilityStatus": "Enabled",
        "appliesTo": "User",
    }


class TestLicenseUsageReport(CommonFailureTests, ReportTestCase):
    script = license_usage
    script_name = LICENSE_USAGE
    graph_path = "/subscribedSkus"

    def seed_one_page(self):
        self.graph.add_collection(self.graph_path, [make_sku(1, "SPE_E3", 10, 5)], page_size=10)

    def seed_four_pages(self):
        skus = [make_sku(n, f"CONTOSO_SKU_{n}", 10, n) for n in range(1, 9)]
        self.graph.add_collection(self.graph_path, skus, page_size=2)

    def write_pricing(self, prices):
        path = os.path.join(self.tmp, "pricing.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prices, f)
        return path

    def test_reads_every_page_and_computes_units_utilization_and_waste(self):
        skus = [
            make_sku(1, "ENTERPRISEPACK", 50, 10),
            make_sku(2, "FLOW_FREE", 10000, 3),
            make_sku(3, "SPE_E3", 100, 80),
            make_sku(4, "CONTOSO_CUSTOM_SKU", 4, 1),
            make_sku(5, "VISIOCLIENT", 0, 0),
            make_sku(6, "AAD_PREMIUM_P2", 10, 12),  # more assigned than enabled
        ]
        self.graph.add_collection(self.graph_path, skus, page_size=2)
        pricing = self.write_pricing({"SPE_E3": 36.0, "ENTERPRISEPACK": 23.0, "AAD_PREMIUM_P2": 9.0, "VISIOCLIENT": 15.0})

        printed = self.run_script("--pricing", pricing)

        header, rows = self.read_csv()
        self.assertEqual(header, documented_columns(self.script_name))
        expected = [
            # SKU, Product, Total, Assigned, Available, Utilization, UnitPrice_USD, MonthlyWaste_USD
            ["SPE_E3", "Microsoft 365 E3", "100", "80", "20", "80.0%", "$36.00", "$720.00"],
            ["AAD_PREMIUM_P2", "Entra ID P2", "10", "12", "-2", "120.0%", "$9.00", "$0.00"],
            ["ENTERPRISEPACK", "Office 365 E3", "50", "10", "40", "20.0%", "$23.00", "$920.00"],
            ["FLOW_FREE", "Power Automate Free", "10000", "3", "9997", "0.0%", "", ""],
            ["CONTOSO_CUSTOM_SKU", "CONTOSO_CUSTOM_SKU", "4", "1", "3", "25.0%", "", ""],
            ["VISIOCLIENT", "Visio Plan 2", "0", "0", "0", "0.0%", "$15.00", "$0.00"],
        ]
        self.assertEqual(rows, [dict(zip(header, values)) for values in expected])

        self.assert_paged_requests(3)
        self.assert_every_request_authorized()
        self.assert_one_app_only_token_request()
        self.assertIn("Found 6 license subscriptions.", printed)
        self.assertIn("  Office 365 E3: 10/50 assigned (20.0%)", printed)
        self.assertIn("  Power Automate Free: 3/10000 assigned (0.0%)", printed)
        self.assertNotIn("Visio Plan 2:", printed)  # 0 units: not "more than 5"
        self.assertNotIn("CONTOSO_CUSTOM_SKU:", printed)  # 4 units: not "more than 5"
        self.assertIn("Estimated monthly waste from unassigned licenses: $1640.00", printed)
        self.assertEqual(header, license_usage.COLUMNS)

    def test_over_assigned_sku_does_not_lower_the_total_waste(self):
        # Control: before the fix the 2 over-assigned units counted as -$18.00 and the total was $702.00.
        skus = [make_sku(1, "SPE_E3", 100, 80), make_sku(2, "AAD_PREMIUM_P2", 10, 12)]
        self.graph.add_collection(self.graph_path, skus, page_size=10)
        pricing = self.write_pricing({"SPE_E3": 36.0, "AAD_PREMIUM_P2": 9.0})

        printed = self.run_script("--pricing", pricing)

        _, rows = self.read_csv()
        waste = {row["SKU"]: row["MonthlyWaste_USD"] for row in rows}
        self.assertEqual(waste, {"SPE_E3": "$720.00", "AAD_PREMIUM_P2": "$0.00"})
        self.assertIn("Estimated monthly waste from unassigned licenses: $720.00", printed)

    def test_without_pricing_the_price_columns_stay_empty(self):
        self.graph.add_collection(self.graph_path, [make_sku(1, "SPE_E3", 100, 80)], page_size=10)

        printed = self.run_script()

        _, rows = self.read_csv()
        self.assertEqual(rows[0]["UnitPrice_USD"], "")
        self.assertEqual(rows[0]["MonthlyWaste_USD"], "")
        self.assertNotIn("Estimated monthly waste", printed)

    def test_missing_sku_fields_count_as_zero(self):
        self.graph.add_collection(self.graph_path, [{"skuId": "00000000-0000-0000-0000-0000000000b9"}], page_size=10)

        self.run_script()

        _, rows = self.read_csv()
        self.assertEqual(rows, [{
            "SKU": "Unknown", "Product": "Unknown", "Total": "0", "Assigned": "0", "Available": "0",
            "Utilization": "0.0%", "UnitPrice_USD": "", "MonthlyWaste_USD": "",
        }])


# ---------------------------------------------------------------- security alerts

def make_alert(number, severity, created, status="new", evidence=None, **overrides):
    """An invented alerts_v2 alert."""
    alert = {
        "id": f"fake-alert-{number:03d}",
        "title": f"Invented alert {number}",
        "severity": severity,
        "status": status,
        "classification": None,
        "category": "InitialAccess",
        "serviceSource": "microsoftDefenderForEndpoint",
        "createdDateTime": created,
        "evidence": evidence if evidence is not None else [],
    }
    alert.update(overrides)
    return alert


def user_evidence(upn):
    return {"@odata.type": "#microsoft.graph.security.userEvidence", "userAccount": {"userPrincipalName": upn}}


def device_evidence(name):
    return {"@odata.type": "#microsoft.graph.security.deviceEvidence", "deviceDnsName": name}


def ip_evidence(address):
    return {"@odata.type": "#microsoft.graph.security.ipEvidence", "ipAddress": address}


class TestSecurityAlertsReport(CommonFailureTests, ReportTestCase):
    script = security_alerts
    script_name = SECURITY_ALERTS
    graph_path = "/security/alerts_v2"

    def seed_one_page(self):
        self.graph.add_collection(self.graph_path, [make_alert(1, "high", "2026-11-20T10:00:00Z")], page_size=10)

    def seed_four_pages(self):
        alerts = [make_alert(n, "low", f"2026-11-{n:02d}T10:00:00Z") for n in range(1, 9)]
        self.graph.add_collection(self.graph_path, alerts, page_size=2)

    def cutoff_in_filter(self, pattern):
        """Match the first request's $filter against pattern; return its cutoff as a datetime."""
        match = re.fullmatch(pattern, self.graph.requests[0]["params"]["$filter"])
        self.assertIsNotNone(match, self.graph.requests[0]["params"]["$filter"])
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    def test_reads_every_page_and_sorts_by_severity_then_created(self):
        alerts = [  # the order Graph is asked for: newest first
            make_alert(1, "low", "2026-11-20T10:00:00Z"),
            make_alert(2, "high", "2026-11-19T10:00:00.1234567Z"),
            make_alert(3, "informational", "2026-11-18T10:00:00Z"),
            make_alert(4, "medium", "2026-11-17T10:00:00Z"),
            make_alert(5, "high", "2026-11-16T10:00:00Z", status="inProgress"),
            make_alert(6, "unknown", "2026-11-15T10:00:00Z"),
            make_alert(7, "medium", "2026-11-14T10:00:00Z", status="resolved"),
        ]
        self.graph.add_collection(self.graph_path, alerts, page_size=3)

        printed = self.run_script()

        header, rows = self.read_csv()
        self.assertEqual(header, documented_columns(self.script_name))
        self.assertEqual([row["AlertId"] for row in rows],
                         [f"fake-alert-{n:03d}" for n in (5, 2, 7, 4, 1, 3, 6)])
        self.assertEqual([row["Severity"] for row in rows],
                         ["High", "High", "Medium", "Medium", "Low", "Informational", "Unknown"])
        second = rows[1]
        self.assertEqual(second["Title"], "Invented alert 2")
        self.assertEqual(second["Status"], "new")
        self.assertEqual(second["Classification"], "")
        self.assertEqual(second["Category"], "InitialAccess")
        self.assertEqual(second["Source"], "microsoftDefenderForEndpoint")
        self.assertEqual(second["Created"], "2026-11-19 10:00:00")

        self.assert_paged_requests(3)
        self.assert_every_request_authorized()
        self.assert_one_app_only_token_request()
        self.assertIn("Found 7 alerts.", printed)
        for line in ("  High: 2", "  Medium: 2", "  Low: 1", "  Informational: 1"):
            self.assertIn(line, printed)
        self.assertNotIn("Unknown:", printed)
        self.assertIn("** 1 HIGH severity alerts still in 'new' status -- needs attention **", printed)
        self.assertEqual(header, security_alerts.COLUMNS)

    def test_severity_and_days_go_into_the_graph_filter(self):
        self.seed_one_page()

        self.run_script("--severity", "high", "--days", "7")

        params = self.graph.requests[0]["params"]
        self.assertEqual(params["$top"], "999")
        self.assertEqual(params["$orderby"], "createdDateTime desc")
        cutoff = self.cutoff_in_filter(r"createdDateTime ge (\S+) and severity eq 'high'")
        expected = datetime.now(timezone.utc) - timedelta(days=7)
        self.assertLess(abs((expected - cutoff).total_seconds()), 300)

    def test_default_filter_is_30_days_and_any_severity(self):
        self.seed_one_page()

        self.run_script()

        cutoff = self.cutoff_in_filter(r"createdDateTime ge (\S+)")
        expected = datetime.now(timezone.utc) - timedelta(days=30)
        self.assertLess(abs((expected - cutoff).total_seconds()), 300)

    def test_affected_entities_lists_up_to_three_users_or_devices(self):
        alerts = [
            make_alert(1, "high", "2026-11-20T10:00:00Z", evidence=[
                user_evidence("user1@contoso.com"), device_evidence("pc-001.contoso.com"), ip_evidence("192.0.2.10"),
            ]),
            make_alert(2, "high", "2026-11-20T11:00:00Z", evidence=[
                user_evidence("user2@contoso.com"), user_evidence("user3@contoso.com"),
                device_evidence("pc-002.contoso.com"), device_evidence("pc-003.contoso.com"),
            ]),
            make_alert(3, "high", "2026-11-20T12:00:00Z", evidence=[ip_evidence("192.0.2.11")]),
            make_alert(4, "high", "2026-11-20T13:00:00Z", evidence=[
                {"@odata.type": "#microsoft.graph.security.userEvidence", "userAccount": {"userPrincipalName": None}},
            ]),
        ]
        self.graph.add_collection(self.graph_path, alerts, page_size=10)

        self.run_script()

        _, rows = self.read_csv()
        entities = {row["AlertId"]: row["AffectedEntities"] for row in rows}
        self.assertEqual(entities, {
            "fake-alert-001": "User: user1@contoso.com; Device: pc-001.contoso.com",
            "fake-alert-002": "User: user2@contoso.com; User: user3@contoso.com; Device: pc-002.contoso.com",
            "fake-alert-003": "",
            "fake-alert-004": "",
        })

    def test_missing_optional_fields_do_not_break_the_report(self):
        self.graph.add_collection(self.graph_path, [{"id": "fake-alert-099"}], page_size=10)

        self.run_script()

        _, rows = self.read_csv()
        self.assertEqual(rows, [{
            "Severity": "Unknown", "Title": "", "Status": "", "Classification": "", "Category": "",
            "Source": "", "Created": "", "AffectedEntities": "", "AlertId": "fake-alert-099",
        }])


# ---------------------------------------------------------------- the harness itself

class TestHarness(unittest.TestCase):
    def test_scripts_use_the_stub_packages(self):
        for module in (inactive_users, license_usage, security_alerts):
            self.assertTrue(getattr(module.msal, "IS_FAKE", False), module.__name__)
            self.assertTrue(getattr(module.requests, "IS_FAKE", False), module.__name__)

    def test_fake_refuses_any_host_but_microsoft_graph(self):
        graph = fake_graph.reset(ACCESS_TOKEN)
        with self.assertRaises(AssertionError):
            graph.get("https://login.microsoftonline.com/" + TENANT_ID, headers={})

    def test_fake_answers_401_without_the_token(self):
        graph = fake_graph.reset(ACCESS_TOKEN)
        graph.add_collection("/users", [], page_size=10)
        self.assertEqual(graph.get(fake_graph.GRAPH_ROOT + "/users", headers={}).status_code, 401)
        self.assertEqual(graph.get(fake_graph.GRAPH_ROOT + "/users",
                                   headers={"Authorization": "Bearer " + ACCESS_TOKEN}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
