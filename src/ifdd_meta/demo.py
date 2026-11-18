"""``ifdd-meta demo load``: the demo user and the example data of ct.

The demo user ``ifdd-demo`` reads and writes every domain context and gets
into wf. Its password comes from ``IFDD_META_DEMO_PASSWORD`` (environment or
``.env`` next to the settings file); without one a new password is generated
and written into that ``.env`` (mode 0600). The password is never shown.

Then the ct pipelines ``generate-energy-grid`` and ``generate-time-series``
load fake data into be and ts-api; they use a fixed seed, so a second run adds
nothing. The services must be running.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import yaml

from . import copysecret, keycloak
from .create import CreateError, load_state
from .runner import Run
from .settings import Settings

USER = "ifdd-demo"
PASSWORD_VARIABLE = "IFDD_META_DEMO_PASSWORD"
GRID = "pipelines/generate-energy-grid.yaml"
SERIES = "pipelines/generate-time-series.yaml"


class DemoError(CreateError):
    """demo load cannot start."""


def groups(variables: dict, services: set[str]) -> list[str]:
    """Readers, writers, privileged, the wf group (with wf) and the context root."""
    names = [
        variables["ifdd_keycloak_group_nodes_readers"],
        variables["ifdd_keycloak_group_nodes_writers"],
        variables["ifdd_keycloak_group_nodes_privileged"],
    ]
    if "wf" in services:
        names.append(variables["ifdd_keycloak_wf_admin_group"])
    names.append(f"ifdd_ctx_{variables['ifdd_context_root']}")
    return names


def password(settings: Settings, run: Run) -> str:
    """The configured password, or a new one written into .env next to the settings."""
    value = settings.secrets.get(PASSWORD_VARIABLE)
    if value:
        return value
    if settings.file is None:
        raise DemoError(f"no settings file: set {PASSWORD_VARIABLE} in the environment")
    target: Path = settings.file.parent / ".env"
    value = secrets.token_urlsafe(18)
    copysecret.set_env(target, PASSWORD_VARIABLE, value)
    run.overview(f"New password of {USER} in {target} ({PASSWORD_VARIABLE})")
    return value


def load(settings: Settings, run: Run, admin_factory=keycloak.Admin.from_settings) -> None:
    state = load_state(settings)
    if "keycloak" not in state:
        raise DemoError(f"no keycloak instance in {settings['workdir']}; run 'create' first")
    variables = yaml.safe_load(settings["variables"].read_text(encoding="utf-8"))["default_context"]
    value = password(settings, run)
    admin = admin_factory(settings)
    _, created = admin.ensure_user(
        USER, value, groups=groups(variables, set(state)), first_name="IFDD", last_name="Demo"
    )
    run.overview(f"Demo user {USER} " + ("created" if created else "updated (password, groups)"))
    if "ct" not in state:
        run.overview("No ct instance: no demo data")
        return
    ct = settings["workdir"] / state["ct"]
    run.overview("Loading the energy grid (ct pipeline generate-energy-grid)")
    run.command(["./adm_02_run.sh", "-f", GRID], cwd=ct)
    if "ts-api" in state:
        run.overview("Loading the time series (ct pipeline generate-time-series)")
        run.command(["./adm_02_run.sh", "-f", SERIES], cwd=ct)
