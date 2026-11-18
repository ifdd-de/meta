"""Fixtures of the integration tests: a running installation, as ifdd-meta sees it.

``ifdd-meta test`` sets IFDD_META_TEST_SETTINGS to the settings file. Values
come from the variables file and the instances; secrets are read from the
instances and never printed. Tests that write data use the fixture ``writes``
and run only with the setting ``tests: write`` (default: ``read``).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from ifdd_meta import envfile, keycloak
from ifdd_meta.create import load_state
from ifdd_meta.http import Client
from ifdd_meta.settings import Settings, load

#: Prefix of the client the tests create for themselves.
TEST_CLIENT = "ifdd-meta-test"
#: Context pattern and store pattern of the test client: only the ops example domain.
TEST_PATTERN = "ops.example.ifdd.de"
#: Clients of the realm whose secrets keycloak/.env holds (variable of the id, of the secret).
CLIENTS = {
    "ct": ("ifdd_keycloak_ct_client_id", "IFDD_KEYCLOAK_CT_CLIENT_SECRET"),
    "api": ("ifdd_keycloak_api_client_id", "IFDD_KEYCLOAK_API_CLIENT_SECRET"),
    "ingest": ("ifdd_keycloak_ts_ingest_client_id", "IFDD_KEYCLOAK_TSINGEST_CLIENT_SECRET"),
}


@dataclass(frozen=True)
class Installation:
    settings: Settings
    variables: dict
    paths: dict[str, Path]  # service -> instance directory
    ca_file: Path | None

    def has(self, *services: str) -> bool:
        return all(service in self.paths for service in services)

    def env(self, service: str) -> dict[str, str]:
        return envfile.read(self.paths[service] / ".env")

    def run(
        self, service: str, *argv: str, stdin: str | None = None, stderr: bool = False,
        timeout: int = 600,
    ) -> str:  # fmt: skip
        """A command in an instance directory; returns stdout (and stderr), never prints it.

        On failure only the last lines of the output are shown: the commands
        used here print no secrets.
        """
        result = subprocess.run(
            argv,
            cwd=self.paths[service],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-20:])
        assert result.returncode == 0, f"{service}: {argv[0]} … failed:\n{tail}"
        return result.stdout + (result.stderr if stderr else "")

    def compose(self, service: str, *args: str, stdin: str | None = None) -> str:
        """``docker compose`` with the Compose file of the environment."""
        environment = self.settings["environment"]
        return self.run(
            service, "docker", "compose", "-f", f"docker-compose-{environment}.yml", *args,
            stdin=stdin,
        )  # fmt: skip


@pytest.fixture(scope="session")
def installation() -> Installation:
    path = os.environ.get("IFDD_META_TEST_SETTINGS")
    if not path:
        pytest.exit("run the integration tests with 'ifdd-meta test'", returncode=2)
    settings = load(settings_file=Path(path), environ=os.environ)
    variables = yaml.safe_load(settings["variables"].read_text(encoding="utf-8"))
    state = load_state(settings)
    paths = {name: settings["workdir"] / directory for name, directory in state.items()}
    ca = paths["proxy"] / "certs" / "ca.crt" if "proxy" in paths else None
    return Installation(
        settings, variables["default_context"], paths, ca if ca and ca.is_file() else None
    )


@pytest.fixture(scope="session")
def writes(installation):
    """Skips the test unless the setting tests is write."""
    if installation.settings["tests"] != "write":
        pytest.skip("writes data: needs the setting tests: write")


@pytest.fixture(scope="session")
def ssl_context(installation):
    return keycloak.ssl_context(installation.ca_file)


@pytest.fixture(scope="session")
def http(ssl_context):
    return Client(ssl_context)


@pytest.fixture(scope="session")
def tokens(installation, ssl_context):
    """Access token of a client of keycloak/.env: tokens("ct"|"api"|"ingest") (cached)."""
    if not installation.has("keycloak"):
        pytest.skip("no keycloak instance")
    env = installation.env("keycloak")
    variables = installation.variables
    cache: dict[str, str] = {}

    def token(name: str) -> str:
        if name not in cache:
            id_variable, secret_key = CLIENTS[name]
            cache[name] = keycloak.client_token(
                ssl_context, variables["ifdd_oidc_realm"], variables[id_variable], env[secret_key]
            )
        return cache[name]

    return token


@pytest.fixture(scope="session")
def test_client(installation, ssl_context, writes):
    """A service client with read and write roles, but only the ops patterns.

    Created through the admin API for this session and deleted afterwards,
    with the pattern roles the fixture had to create. Yields its access token.
    """
    if not installation.has("keycloak"):
        pytest.skip("no keycloak instance")
    variables = installation.variables
    admin = keycloak.Admin.from_instance(
        installation.paths["keycloak"], variables["ifdd_keycloak_realm"], installation.ca_file
    )
    client_id = f"{TEST_CLIENT}-{uuid.uuid4().hex[:8]}"
    nodes_read = variables["ifdd_be_role_nodes_read"]
    roles = {
        variables["ifdd_oidc_backend_client_id"]: [
            nodes_read, variables["ifdd_be_role_nodes_write"], TEST_PATTERN,
        ],
        variables["ifdd_ts_api_oidc_client_id"]: [
            variables["ifdd_ts_api_role_read"], variables["ifdd_ts_api_role_write"], TEST_PATTERN,
        ],
    }  # fmt: skip
    # The scope named like the read role adds the backend to the audience (realm generator).
    client = admin.add_service_client(
        client_id, roles, default_scopes=[nodes_read], create_roles=True
    )
    try:
        yield keycloak.client_token(
            ssl_context, variables["ifdd_oidc_realm"], client_id, admin.client_secret(client)
        )
    finally:
        admin.delete_client(client)
        for owner, role in admin.created_roles:
            admin.delete_client_role(owner, role)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
