"""What ifdd-meta knows about the templates (data, not configuration)."""

from __future__ import annotations

from .settings import SERVICES

#: Version variables come from the template, never from the variables file (rule B.3).
VERSION_VARIABLES = frozenset(
    {
        "ifdd_be_version",
        "ifdd_be_core_version",
        "ifdd_ui_version",
        "ifdd_ct_version",
        "ifdd_ts_api_version",
        "ifdd_ts_core_version",
    }
)

#: First label of the public host name of a service; services without a host are missing.
HOST_LABELS = {
    "keycloak": "id",
    "be": "rest",
    "ui": "ui",
    "schema": "schema",
    "type": "type",
    "unit": "unit",
    "ts": "ts",
    "wf": "wf",
}
#: Host label of the Traefik dashboard; it gets no network alias and no DNS record.
DASHBOARD_LABEL = "proxy"

#: Endings of variable names that would hold a secret; none may be a template variable.
SECRET_ENDINGS = ("_secret", "_password", "_token")

#: Paths that ``cruft update`` must not touch, as the template READMEs and docs recommend.
SKIPS = {
    "proxy": (".env", "secrets", "certs", "letsencrypt", "logs", "traefik/private"),
    "keycloak": (".env",),
    "be": (".env", "envs", "certs", "app/routers", "app/settings.py", "tests"),
    "ui": (".env", "envs", "src/private", "src/Env.js", "certs"),
    "schema": (".env", "examples"),
    "type": (".env", "examples"),
    "unit": (".env", "examples"),
    "ct": (".env", "certs", "data", "plugins", "private", "tests/private"),
    "ts": (".env", "secrets", "grafana/dashboards/private"),
    "ts-api": (".env", "certs"),
    "wf": (".env", "secrets", "certs"),
    "model": (".env", "src/linkml", "tests/reference"),
}

#: Services that call others through the proxy and need its local CA in certs/.
CA_USERS = ("be", "ct", "ts", "ts-api", "wf")

#: Optional wheels of unreleased builds: package -> (service, file pattern).
WHEELS = {
    "be-core": ("be", "ifdd_be_core-*.whl"),
    "ts-core": ("ts-api", "ifdd_ts_core-*.whl"),
}


#: Client secrets from keycloak/.env to the instances that use them:
#: (service, variable in keycloak/.env, kind, path in the instance, variable there).
#: kind "file": whole secret file; "env": line of a .env file; "kestra": SECRET_<name>, base64.
SECRET_COPIES = (
    (
        "proxy",
        "IFDD_KEYCLOAK_PROXY_CLIENT_SECRET",
        "file",
        "secrets/oauth2_proxy_client_secret",
        "",
    ),
    ("ts", "IFDD_KEYCLOAK_TS_CLIENT_SECRET", "file", "secrets/oidc_client_secret", ""),
    ("ct", "IFDD_KEYCLOAK_CT_CLIENT_SECRET", "env", ".env", "IFDD_CT_OIDC_CLIENT_SECRET"),
    (
        "wf",
        "IFDD_KEYCLOAK_API_CLIENT_SECRET",
        "kestra",
        "secrets/kestra.env",
        "IFDD_API_CLIENT_SECRET",
    ),
)

#: Files that survive "down --purge" (relative to the instance), e.g. the ACME state.
PERSIST = {"proxy": ("letsencrypt/acme.json",)}

#: Hooks of these templates refuse to run as root.
NO_ROOT = ("keycloak", "ts")

__all__ = [
    "CA_USERS",
    "DASHBOARD_LABEL",
    "HOST_LABELS",
    "NO_ROOT",
    "PERSIST",
    "SECRET_COPIES",
    "SECRET_ENDINGS",
    "SERVICES",
    "SKIPS",
    "VERSION_VARIABLES",
    "WHEELS",
]
