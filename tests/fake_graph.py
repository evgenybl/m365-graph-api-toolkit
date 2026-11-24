"""In-memory stand-ins for msal, requests and Microsoft Graph, used by the tests.

Nothing in this file opens a network connection. install() puts stub modules
named "msal" and "requests" into sys.modules, so the report scripts import the
stubs whether or not the real packages are installed. All data is invented.

Run as a program, it executes one report script as __main__ against the fake:

    python tests/fake_graph.py <scenario> <path/to/script.py> [script arguments...]

Scenarios: "token-failure" (the token request is refused) and "empty" (every
Graph collection the scripts read is empty).
"""

import json
import runpy
import sys
import types
from urllib.parse import parse_qs, urlsplit

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
SKIPTOKEN = "$skiptoken"


def graph_error(code, message):
    """An error body in the shape Microsoft Graph uses."""
    return {"error": {"code": code, "message": message}}


class FakeResponse:
    """The parts of requests.Response the scripts use."""

    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.text = json.dumps(body)

    def json(self):
        # A fresh copy on every call, so a script cannot change the fake's data.
        return json.loads(self.text)


class FakeGraph:
    """Serves canned JSON for Graph paths, page by page, and records every request."""

    def __init__(self, token):
        self.token = token
        self.collections = {}  # path -> list of pages, each a list of records
        self.errors = {}  # (path, page index) -> FakeResponse
        self.requests = []  # one dict per request: url, headers, params, kwargs

    def add_collection(self, path, records, page_size):
        """Serve records at path, page_size per page, linked by @odata.nextLink."""
        pages = [records[i:i + page_size] for i in range(0, len(records), page_size)]
        self.collections[path] = pages or [[]]

    def fail(self, path, status, body, page=0, headers=None):
        """Answer the given page of path with an HTTP error instead of data."""
        self.errors[(path, page)] = FakeResponse(status, body, headers)

    def get(self, url, headers=None, params=None, **kwargs):
        headers = dict(headers or {})
        self.requests.append({
            "url": url,
            "headers": headers,
            "params": dict(params) if params else None,
            "kwargs": dict(kwargs),
        })
        if not url.startswith(GRAPH_ROOT + "/"):
            raise AssertionError("the fake serves only Microsoft Graph v1.0, not " + url)
        if headers.get("Authorization") != "Bearer " + self.token:
            return FakeResponse(401, graph_error("InvalidAuthenticationToken", "Access token is missing or invalid."))

        parts = urlsplit(url)
        path = parts.path[len("/v1.0"):]
        token = parse_qs(parts.query).get(SKIPTOKEN, ["page-0"])[0]
        page = int(token[len("page-"):])

        if (path, page) in self.errors:
            return self.errors[(path, page)]
        if path not in self.collections:
            return FakeResponse(404, graph_error("Request_ResourceNotFound", "Resource not found."))

        pages = self.collections[path]
        body = {"@odata.context": GRAPH_ROOT + "/$metadata#" + path.strip("/"), "value": pages[page]}
        if page + 1 < len(pages):
            body["@odata.nextLink"] = f"{GRAPH_ROOT}{path}?{SKIPTOKEN}=page-{page + 1}"
        return FakeResponse(200, body)


class _State:
    """What the stub modules serve, and what they were asked."""

    def __init__(self):
        self.graph = None
        self.token_result = None
        self.apps = []  # one dict per ConfidentialClientApplication created
        self.scopes = []  # the scopes of each token request


STATE = _State()


def reset(token):
    """Start a fresh fake Graph that accepts token; token requests succeed with it."""
    STATE.graph = FakeGraph(token)
    STATE.token_result = {"token_type": "Bearer", "expires_in": 3599, "access_token": token}
    STATE.apps = []
    STATE.scopes = []
    return STATE.graph


def fail_token(description):
    """Make the next token request answer with an error, as msal reports one."""
    STATE.token_result = {"error": "invalid_client", "error_description": description}


class FakeConfidentialClientApplication:
    """Stands in for msal.ConfidentialClientApplication; never contacts a server."""

    def __init__(self, client_id, authority=None, client_credential=None, **kwargs):
        STATE.apps.append({
            "client_id": client_id,
            "authority": authority,
            "client_credential": client_credential,
        })

    def acquire_token_for_client(self, scopes, **kwargs):
        STATE.scopes.append(list(scopes))
        return dict(STATE.token_result)


def _get(url, params=None, **kwargs):
    return STATE.graph.get(url, params=params, **kwargs)


def install():
    """Put the stub msal and requests modules into sys.modules."""
    msal = types.ModuleType("msal")
    msal.ConfidentialClientApplication = FakeConfidentialClientApplication
    msal.IS_FAKE = True

    requests = types.ModuleType("requests")
    requests.get = _get
    requests.IS_FAKE = True

    sys.modules["msal"] = msal
    sys.modules["requests"] = requests


def _run_as_program(argv):
    scenario, script, script_args = argv[0], argv[1], argv[2:]
    install()
    graph = reset("fake" + "-" + "program" + "-" + "token")
    if scenario == "token-failure":
        fail_token("Invented error for the tests: the client secret was rejected.")
    elif scenario == "empty":
        for path in ("/users", "/subscribedSkus", "/security/alerts_v2"):
            graph.add_collection(path, [], page_size=10)
    else:
        raise SystemExit("unknown scenario: " + scenario)
    sys.argv = [script] + script_args
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    _run_as_program(sys.argv[1:])
