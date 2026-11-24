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
    "lab": "lab",
    "model": "model",
}
#: Services a service calls; compile stops unless each is local or external.
DEPENDENCIES = {
    "be": ("keycloak", "schema"),
    "ui": ("keycloak", "be", "type", "unit"),
    "ts": ("keycloak", "be"),
    "ts-api": ("keycloak", "type", "ts"),
    "ct": ("keycloak", "be", "schema"),
    "wf": ("keycloak", "be"),
    "lab": ("keycloak", "be"),
}
#: Services a service needs on the same server (ts-api writes into the ingest network of ts).
LOCAL_DEPENDENCIES = {"ts-api": ("ts",)}
#: Host label of the Traefik dashboard; no network alias, and a DNS record (hosts.txt)
#: only when the proxy block names its host.
DASHBOARD_LABEL = "proxy"
#: Host label of the local Git server for the wf flows (setting wf_git: local).
GIT_LABEL = "git"
#: User and repository of the wf flows on the local Git server.
GIT_USER = "kestra"
GIT_REPOSITORY = "flows"

#: Endings of variable names that would hold a secret; none may be a template variable.
SECRET_ENDINGS = ("_secret", "_password", "_token")

#: Keycloak client of the lab hub, set by compile when lab is among the services.
LAB_CLIENT_ID = "ifdd-lab"

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
    "lab": (".env", "secrets", "certs", "hub/config.d"),
    "model": (".env", "src/linkml", "tests/reference"),
}

#: Services that call others through the proxy and need its local CA in certs/.
CA_USERS = ("be", "ct", "ts", "ts-api", "wf", "lab")
#: Settings that name the file of the local CA inside the container: (service,
#: file, variable). With Let's Encrypt there is no such file, so create empties
#: them and the service uses the system trust store.
CA_FILE_SETTINGS = (("ct", ".env", "IFDD_CT_CA_BUNDLE"),)

#: Optional wheels of unreleased builds: package -> (service, file pattern).
WHEELS = {
    "be-core": ("be", "ifdd_be_core-*.whl"),
    "ts-core": ("ts-api", "ifdd_ts_core-*.whl"),
}


#: Client secrets from keycloak/.env to the instances that use them:
#: (service, variable in keycloak/.env, kind, path in the instance, variable there).
#: kind "file": whole secret file, if the template has it; "new-file": whole secret file,
#: created (0600) if missing; "env": line of a .env file; "kestra": SECRET_<name>, base64.
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
    # The template has no such file (its README creates it with mode 0600).
    ("lab", "IFDD_KEYCLOAK_LAB_CLIENT_SECRET", "new-file", "secrets/oidc_client_secret", ""),
)

#: Client secrets that 'ifdd-meta keycloak secrets' passes to the server of another
#: realm: those the services take (SECRET_COPIES) and those the integration tests use.
#: Never the user admin: its manage-users can grant every realm role.
KEYCLOAK_EXPORTS = tuple(dict.fromkeys(
    [variable for _, variable, *_ in SECRET_COPIES] + ["IFDD_KEYCLOAK_TSINGEST_CLIENT_SECRET"]
))  # fmt: skip
#: File names of the CA certificates in certs/ of the CA users: the local CA, the
#: bundle of all for settings that take one file (CA_FILE_SETTINGS), the extra ones.
CA_BUNDLE = "ca-bundle.crt"
EXTRA_CA_PREFIX = "extra-"

#: Files and directories that survive "down --purge" (relative to the instance): the
#: ACME state, the database dumps of keycloak (added to, never deleted, and put back
#: into the next instance for adm_backup_restore.sh).
PERSIST = {"proxy": ("letsencrypt/acme.json",), "keycloak": ("backups",)}
#: Docker volumes (Compose names) that survive "down --purge"; the next instance of
#: the same name finds them again. lab: the Docker-in-Docker daemon with the homes.
PERSIST_VOLUMES = {"lab": ("dind-data",)}

#: Hooks of these templates refuse to run as root.
NO_ROOT = ("keycloak", "ts")

__all__ = [
    "CA_BUNDLE",
    "CA_FILE_SETTINGS",
    "CA_USERS",
    "DASHBOARD_LABEL",
    "DEPENDENCIES",
    "EXTRA_CA_PREFIX",
    "GIT_LABEL",
    "GIT_REPOSITORY",
    "GIT_USER",
    "HOST_LABELS",
    "KEYCLOAK_EXPORTS",
    "LOCAL_DEPENDENCIES",
    "LAB_CLIENT_ID",
    "NO_ROOT",
    "PERSIST",
    "PERSIST_VOLUMES",
    "SECRET_COPIES",
    "SECRET_ENDINGS",
    "SERVICES",
    "SKIPS",
    "VERSION_VARIABLES",
    "WHEELS",
]
