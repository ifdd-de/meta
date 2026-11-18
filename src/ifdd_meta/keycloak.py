"""A few calls of the Keycloak admin API, for ``ifdd-meta keycloak`` and the tests.

Login as the admin of the realm master (``admin-cli``, password grant) with the
user and password of ``keycloak/.env``. Service clients (client credentials,
RFC 9700) follow the former ``.adm_tools/keycloak_add_client.py``: roles go to
the service account and, because ``fullScopeAllowed`` is off, into the scope
mapping as well. Users get a password, groups, realm and client roles.
Secrets and passwords are never printed. Standard library only.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import envfile
from .create import load_state
from .settings import Settings

TIMEOUT = 30
BLOCKED_ROLE_CLIENTS = {"realm-management"}


class KeycloakError(RuntimeError):
    """A call failed; the message never holds a secret."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def ssl_context(ca_file: Path | None) -> ssl.SSLContext:
    """System CAs, plus the local CA of the proxy if there is one."""
    context = ssl.create_default_context()
    if ca_file and ca_file.is_file():
        context.load_verify_locations(cafile=str(ca_file))
    return context


def request(
    context: ssl.SSLContext,
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: Any = None,
    form: dict[str, str] | None = None,
) -> tuple[Any, dict[str, str]]:
    """One HTTP call; returns (JSON or None, headers)."""
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    call = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=TIMEOUT, context=context) as response:
            raw = response.read()
            return (json.loads(raw) if raw else None), dict(response.headers)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        path = urllib.parse.urlsplit(url).path
        raise KeycloakError(f"{method} {path}: HTTP {error.code} {detail}", error.code) from None
    except urllib.error.URLError as error:
        raise KeycloakError(f"{method} {url}: {error.reason}") from None


def client_token(context: ssl.SSLContext, realm_url: str, client_id: str, secret: str) -> str:
    """Access token of a confidential client (client credentials)."""
    answer, _ = request(
        context,
        "POST",
        f"{realm_url}/protocol/openid-connect/token",
        form={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
    )
    return answer["access_token"]


class Admin:
    """Admin API of one realm."""

    def __init__(self, base_url: str, realm: str, user: str, password: str, context):
        self.base = base_url.rstrip("/")
        self.realm_admin = f"{self.base}/admin/realms/{urllib.parse.quote(realm)}"
        self.context = context
        self._user, self._password = user, password
        self.token = self._login()
        #: (client id, role) of every role this object created, e.g. to remove them again.
        self.created_roles: list[tuple[str, str]] = []

    @classmethod
    def from_instance(cls, keycloak_dir: Path, realm: str, ca_file: Path | None) -> Admin:
        env = envfile.read(keycloak_dir / ".env")
        keys = ("IFDD_KEYCLOAK_HOST", "IFDD_KEYCLOAK_ADMIN_USER", "IFDD_KEYCLOAK_ADMIN_PASSWORD")
        for key in keys:
            if not env.get(key):
                raise KeycloakError(f"{key} missing in {keycloak_dir / '.env'}")
        return cls(
            f"https://{env['IFDD_KEYCLOAK_HOST']}",
            realm,
            env["IFDD_KEYCLOAK_ADMIN_USER"],
            env["IFDD_KEYCLOAK_ADMIN_PASSWORD"],
            ssl_context(ca_file),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Admin:
        """The admin API of the realm of an installation created by ifdd-meta."""
        state = load_state(settings)
        if "keycloak" not in state:
            raise KeycloakError("no keycloak instance; run 'ifdd-meta create' first")
        variables = yaml.safe_load(settings["variables"].read_text(encoding="utf-8"))
        realm = variables["default_context"]["ifdd_keycloak_realm"]
        ca = settings["workdir"] / state["proxy"] / "certs" / "ca.crt" if "proxy" in state else None
        return cls.from_instance(settings["workdir"] / state["keycloak"], realm, ca)

    def _login(self) -> str:
        try:
            answer, _ = request(
                self.context,
                "POST",
                f"{self.base}/realms/master/protocol/openid-connect/token",
                form={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": self._user,
                    "password": self._password,
                },
            )
        except KeycloakError as error:
            raise KeycloakError(f"login as {self._user} (master) failed: {error}") from None
        return answer["access_token"]

    def call(self, method: str, path: str, body: Any = None) -> tuple[Any, dict[str, str]]:
        """One admin call; logs in again once if the token has expired (401).

        Admin tokens of the master realm are short-lived; a test session that
        takes longer than they live otherwise fails while cleaning up.
        """
        url = self.realm_admin + path
        try:
            return request(self.context, method, url, token=self.token, body=body)
        except KeycloakError as error:
            if error.status != 401:
                raise
        self.token = self._login()
        return request(self.context, method, url, token=self.token, body=body)

    def client_uuid(self, client_id: str) -> str | None:
        found, _ = self.call("GET", f"/clients?clientId={urllib.parse.quote(client_id)}")
        return found[0]["id"] if found else None

    def ensure_client_role(self, client_id: str, role: str) -> dict:
        """The role of a client; created if missing (e.g. a context pattern)."""
        owner = self._require_client(client_id)
        path = f"/clients/{owner}/roles/{urllib.parse.quote(role, safe='')}"
        try:
            found, _ = self.call("GET", path)
            return found
        except KeycloakError as error:
            if error.status != 404:
                raise
        self.call("POST", f"/clients/{owner}/roles", {"name": role})
        self.created_roles.append((client_id, role))
        found, _ = self.call("GET", path)
        return found

    def add_service_client(
        self,
        client_id: str,
        roles: dict[str, list[str]],
        default_scopes: list[str] = (),
        create_roles: bool = False,
    ) -> str:
        """Create a service client; returns its internal id. Fails if it exists."""
        if self.client_uuid(client_id):
            raise KeycloakError(f"client {client_id} exists already")
        resolved = self._resolve_roles(roles, create_roles)
        scope_ids = self._scope_ids(list(default_scopes))
        self.call(
            "POST",
            "/clients",
            {
                "clientId": client_id,
                "protocol": "openid-connect",
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "fullScopeAllowed": False,
            },
        )
        uuid = self.client_uuid(client_id)
        try:
            for scope_id in scope_ids:
                self.call("PUT", f"/clients/{uuid}/default-client-scopes/{scope_id}")
            user, _ = self.call("GET", f"/clients/{uuid}/service-account-user")
            for owner, representations in resolved.items():
                owner_uuid = self._require_client(owner)
                mappings = f"/users/{user['id']}/role-mappings/clients/{owner_uuid}"
                self.call("POST", mappings, representations)
                scopes = f"/clients/{uuid}/scope-mappings/clients/{owner_uuid}"
                self.call("POST", scopes, representations)
        except KeycloakError:
            self.call("DELETE", f"/clients/{uuid}")
            raise
        return uuid

    def user_uuid(self, username: str) -> str | None:
        name = urllib.parse.quote(username)
        found, _ = self.call("GET", f"/users?username={name}&exact=true")
        return found[0]["id"] if found else None

    def add_user(
        self,
        username: str,
        password: str,
        *,
        groups: list[str] = (),
        realm_roles: list[str] = (),
        roles: dict[str, list[str]] | None = None,
        create_roles: bool = False,
        temporary: bool = False,
        first_name: str = "",
        last_name: str = "ifdd-meta",
    ) -> str:
        """Create an enabled user with a password; returns its id. Fails if it exists.

        The user gets a verified example address and a name (first name: the
        user name unless given), so the user profile of Keycloak asks for
        nothing at the first login.
        """
        if self.user_uuid(username):
            raise KeycloakError(f"user {username} exists already")
        resolved = self._resolve_roles(roles or {}, create_roles)
        group_ids = [self._group_id(name) for name in groups]
        realm = [self._realm_role(name) for name in realm_roles]
        self.call(
            "POST",
            "/users",
            {
                "username": username,
                "enabled": True,
                "email": f"{username}@example.org",
                "emailVerified": True,
                "firstName": first_name or username,
                "lastName": last_name,
                "credentials": [{"type": "password", "value": password, "temporary": temporary}],
            },
        )
        uuid = self.user_uuid(username)
        try:
            for group_id in group_ids:
                self.call("PUT", f"/users/{uuid}/groups/{group_id}")
            if realm:
                self.call("POST", f"/users/{uuid}/role-mappings/realm", realm)
            for owner, representations in resolved.items():
                owner_uuid = self._require_client(owner)
                mappings = f"/users/{uuid}/role-mappings/clients/{owner_uuid}"
                self.call("POST", mappings, representations)
        except KeycloakError:
            self.call("DELETE", f"/users/{uuid}")
            raise
        return uuid

    def ensure_user(
        self,
        username: str,
        password: str,
        *,
        groups: list[str] = (),
        realm_roles: list[str] = (),
        first_name: str = "",
        last_name: str = "ifdd-meta",
    ) -> tuple[str, bool]:
        """Create the user, or set the password and add missing groups and realm roles.

        Returns (id, created). Groups and roles the user has beyond these stay.
        """
        uuid = self.user_uuid(username)
        if uuid is None:
            created = self.add_user(
                username, password, groups=list(groups), realm_roles=list(realm_roles),
                first_name=first_name, last_name=last_name,
            )  # fmt: skip
            return created, True
        group_ids = [self._group_id(name) for name in groups]
        realm = [self._realm_role(name) for name in realm_roles]
        self.call(
            "PUT",
            f"/users/{uuid}/reset-password",
            {"type": "password", "value": password, "temporary": False},
        )
        for group_id in group_ids:
            self.call("PUT", f"/users/{uuid}/groups/{group_id}")
        if realm:  # Keycloak keeps a role granted twice once
            self.call("POST", f"/users/{uuid}/role-mappings/realm", realm)
        return uuid, False

    def delete_user(self, uuid: str) -> None:
        self.call("DELETE", f"/users/{uuid}")

    def client_secret(self, uuid: str) -> str:
        answer, _ = self.call("GET", f"/clients/{uuid}/client-secret")
        return answer["value"]

    def delete_client(self, uuid: str) -> None:
        self.call("DELETE", f"/clients/{uuid}")

    def delete_client_role(self, client_id: str, role: str) -> None:
        owner = self._require_client(client_id)
        self.call("DELETE", f"/clients/{owner}/roles/{urllib.parse.quote(role, safe='')}")

    def _require_client(self, client_id: str) -> str:
        uuid = self.client_uuid(client_id)
        if not uuid:
            raise KeycloakError(f"client {client_id} not found")
        return uuid

    def _existing_role(self, client_id: str, role: str) -> dict:
        owner = self._require_client(client_id)
        found, _ = self.call("GET", f"/clients/{owner}/roles/{urllib.parse.quote(role, safe='')}")
        return found

    def _resolve_roles(self, roles: dict[str, list[str]], create: bool) -> dict[str, list[dict]]:
        """Representations of client roles; missing ones are created with ``create``."""
        for owner in roles:
            if owner in BLOCKED_ROLE_CLIENTS:
                raise KeycloakError(f"roles of {owner} are blocked")
        lookup = self.ensure_client_role if create else self._existing_role
        return {owner: [lookup(owner, role) for role in names] for owner, names in roles.items()}

    def _group_id(self, name: str) -> str:
        """Id of a top-level group with exactly this name."""
        found, _ = self.call("GET", f"/groups?search={urllib.parse.quote(name)}&exact=true")
        for group in found or []:
            if group["name"] == name:
                return group["id"]
        raise KeycloakError(f"group {name} not found")

    def _realm_role(self, name: str) -> dict:
        found, _ = self.call("GET", f"/roles/{urllib.parse.quote(name, safe='')}")
        return found

    def _scope_ids(self, names: list[str]) -> list[str]:
        if not names:
            return []
        scopes, _ = self.call("GET", "/client-scopes")
        by_name = {scope["name"]: scope["id"] for scope in scopes}
        missing = [name for name in names if name not in by_name]
        if missing:
            raise KeycloakError(f"client scopes not found: {', '.join(missing)}")
        return [by_name[name] for name in names]
