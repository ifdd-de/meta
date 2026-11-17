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

__all__ = ["DASHBOARD_LABEL", "HOST_LABELS", "SECRET_ENDINGS", "SERVICES", "VERSION_VARIABLES"]
