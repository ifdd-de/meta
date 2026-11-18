"""keycloak.py against a fake Keycloak (only the admin calls ifdd-meta makes).

The fake follows the Keycloak admin REST API as ifdd-meta uses it; the real
API is exercised by the integration run (fixture test_client).
"""

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifdd_meta import keycloak

ADMIN_TOKEN = "admin-token"
REALM = "ifdd"


class FakeKeycloak:
    def __init__(self):
        self.clients = {
            "u-ifdd": {"clientId": "ifdd", "roles": {"ifdd_nodes_ro": {}, "ifdd_nodes_rw": {}}},
            "u-rm": {"clientId": "realm-management", "roles": {"manage-users": {}}},
        }
        self.scopes = {"s-ro": "ifdd_nodes_ro", "s-roles": "roles"}
        self.calls: list[tuple[str, str]] = []
        self.fail: str | None = None  # regex of a path that answers 500
        self.mappings: list[tuple[str, str, list[str]]] = []
        self.default_scopes: list[tuple[str, str]] = []

    def answer(self, method, path, query, body):
        self.calls.append((method, path))
        if self.fail and re.search(self.fail, path):
            return 500, {"error": "injected"}
        if path == "/realms/master/protocol/openid-connect/token":
            if body.get("username") == ["admin"] and body.get("password") == ["pw"]:
                return 200, {"access_token": ADMIN_TOKEN}
            return 401, {"error": "invalid_grant"}
        if path == f"/realms/{REALM}/protocol/openid-connect/token":
            for client in self.clients.values():
                if [client["clientId"]] == body.get("client_id"):
                    if [client.get("secret")] == body.get("client_secret"):
                        return 200, {"access_token": f"token-of-{client['clientId']}"}
            return 401, {"error": "unauthorized_client"}
        return self.admin(method, path.removeprefix(f"/admin/realms/{REALM}"), query, body)

    def admin(self, method, path, query, body):
        if path == "/clients" and method == "GET":
            wanted = query.get("clientId", [None])[0]
            return 200, [
                {"id": uuid, "clientId": c["clientId"]}
                for uuid, c in self.clients.items()
                if c["clientId"] == wanted
            ]
        if path == "/clients" and method == "POST":
            uuid = f"u-{len(self.clients)}"
            self.clients[uuid] = {**body, "roles": {}, "secret": f"secret-{uuid}"}
            return 201, None
        if path == "/client-scopes":
            return 200, [{"id": i, "name": n} for i, n in self.scopes.items()]
        match = re.fullmatch(r"/clients/([^/]+)(/.*)?", path)
        if not match or match.group(1) not in self.clients:
            return 404, {"error": "not found"}
        uuid, rest = match.group(1), match.group(2) or ""
        client = self.clients[uuid]
        if rest == "" and method == "DELETE":
            del self.clients[uuid]
            return 204, None
        if rest == "/roles" and method == "POST":
            client["roles"][body["name"]] = {}
            return 201, None
        role = re.fullmatch(r"/roles/(.+)", rest)
        if role:
            name = urllib.parse.unquote(role.group(1))
            if name not in client["roles"]:
                return 404, {"error": "role not found"}
            if method == "DELETE":
                del client["roles"][name]
                return 204, None
            return 200, {"id": f"r-{name}", "name": name}
        scope = re.fullmatch(r"/default-client-scopes/(.+)", rest)
        if scope and method == "PUT":
            self.default_scopes.append((uuid, scope.group(1)))
            return 204, None
        if rest == "/service-account-user":
            return 200, {"id": f"user-{uuid}"}
        if rest == "/client-secret":
            return 200, {"value": client["secret"]}
        mapping = re.fullmatch(r"/scope-mappings/clients/(.+)", rest)
        if mapping and method == "POST":
            self.mappings.append(("scope", mapping.group(1), [r["name"] for r in body]))
            return 204, None
        return 404, {"error": "unknown"}

    def users(self, method, path, body):
        match = re.fullmatch(rf"/admin/realms/{REALM}/users/[^/]+/role-mappings/clients/(.+)", path)
        if match and method == "POST":
            self.mappings.append(("user", match.group(1), [r["name"] for r in body]))
            return 204, None
        return None


@pytest.fixture
def fake():
    state = FakeKeycloak()

    class Handler(BaseHTTPRequestHandler):
        def handle_any(self):
            url = urllib.parse.urlsplit(self.path)
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.headers.get("Content-Type", "").startswith("application/x-www-form"):
                body = urllib.parse.parse_qs(raw.decode())
            else:
                body = json.loads(raw) if raw else None
            if url.path.startswith("/admin/") and (
                self.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}"
            ):
                status, payload = 401, {"error": "no admin token"}
            else:
                users = state.users(self.command, url.path, body)
                status, payload = users or state.answer(
                    self.command, url.path, urllib.parse.parse_qs(url.query), body or {}
                )
            data = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = handle_any

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def admin(fake, password="pw"):
    return keycloak.Admin(fake.url, REALM, "admin", password, keycloak.ssl_context(None))


def test_wrong_admin_password_is_reported_without_it(fake):
    with pytest.raises(keycloak.KeycloakError) as error:
        admin(fake, password="wrong-password")
    assert "login as admin (master) failed" in str(error.value)
    assert "wrong-password" not in str(error.value)


def test_add_service_client_maps_roles_scopes_and_patterns(fake):
    api = admin(fake)
    uuid = api.add_service_client(
        "probe",
        {"ifdd": ["ifdd_nodes_ro", "ops.example.ifdd.de"]},
        default_scopes=["ifdd_nodes_ro"],
        create_roles=True,
    )
    created = fake.clients[uuid]
    assert created["clientId"] == "probe"
    assert created["serviceAccountsEnabled"] is True
    assert created["fullScopeAllowed"] is False
    assert created["publicClient"] is False
    assert fake.default_scopes == [(uuid, "s-ro")]
    roles = ["ifdd_nodes_ro", "ops.example.ifdd.de"]
    assert fake.mappings == [("user", "u-ifdd", roles), ("scope", "u-ifdd", roles)]
    assert api.created_roles == [("ifdd", "ops.example.ifdd.de")]
    token = keycloak.client_token(
        keycloak.ssl_context(None), f"{fake.url}/realms/{REALM}", "probe", api.client_secret(uuid)
    )
    assert token == "token-of-probe"


def test_missing_role_is_an_error_without_create_roles(fake):
    with pytest.raises(keycloak.KeycloakError, match="HTTP 404"):
        admin(fake).add_service_client("probe", {"ifdd": ["ops.example.ifdd.de"]})
    assert [c["clientId"] for c in fake.clients.values()] == ["ifdd", "realm-management"]


def test_existing_client_and_blocked_roles_are_refused(fake):
    api = admin(fake)
    with pytest.raises(keycloak.KeycloakError, match="exists already"):
        api.add_service_client("ifdd", {})
    with pytest.raises(keycloak.KeycloakError, match="realm-management are blocked"):
        api.add_service_client("probe", {"realm-management": ["manage-users"]})


def test_unknown_scope_is_refused_before_the_client_exists(fake):
    with pytest.raises(keycloak.KeycloakError, match="client scopes not found: nope"):
        admin(fake).add_service_client("probe", {}, default_scopes=["nope"])
    assert ("POST", f"/admin/realms/{REALM}/clients") not in fake.calls


def test_a_failed_mapping_removes_the_new_client(fake):
    fake.fail = r"/scope-mappings/"
    with pytest.raises(keycloak.KeycloakError, match="HTTP 500"):
        admin(fake).add_service_client("probe", {"ifdd": ["ifdd_nodes_ro"]})
    assert "probe" not in [c["clientId"] for c in fake.clients.values()]


def test_created_roles_can_be_removed_again(fake):
    api = admin(fake)
    uuid = api.add_service_client("probe", {"ifdd": ["new.pattern"]}, create_roles=True)
    api.delete_client(uuid)
    for owner, role in api.created_roles:
        api.delete_client_role(owner, role)
    assert set(fake.clients["u-ifdd"]["roles"]) == {"ifdd_nodes_ro", "ifdd_nodes_rw"}
    assert uuid not in fake.clients
