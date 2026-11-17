"""``ifdd-meta create``: the instances of an installation, ready to build.

Per service ``cruft create`` with the skip list and the chosen reference; then
optional wheels, the local CA (TLS mode ``local-ca``) and the client secrets
from ``keycloak/.env``. Existing instances are never overwritten.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from . import copysecret
from .catalogue import CA_USERS, NO_ROOT, PERSIST, SECRET_COPIES, SERVICES, SKIPS, WHEELS
from .runner import Run, StepError
from .settings import Settings
from .templates import location

STATE_DIR = ".ifdd-meta"
WF_GIT_TOKEN = "IFDD_META_WF_GIT_TOKEN"
STATE_FILE = "instances.json"


class CreateError(RuntimeError):
    """create cannot start or continue; nothing is overwritten."""


def state_path(settings: Settings) -> Path:
    return settings["workdir"] / STATE_DIR / STATE_FILE


def load_state(settings: Settings) -> dict[str, str]:
    """Instance directory (relative to the work directory) per service."""
    path = state_path(settings)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(settings: Settings, state: dict[str, str]) -> None:
    path = state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {service: state[service] for service in SERVICES if service in state}
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def select(settings: Settings, only: Iterable[str] | None) -> tuple[str, ...]:
    if not only:
        return settings["services"]
    wanted = set(only)
    unknown = wanted - set(settings["services"])
    if unknown:
        raise CreateError(f"not among the services of the settings: {', '.join(sorted(unknown))}")
    return tuple(service for service in settings["services"] if service in wanted)


def create(
    settings: Settings,
    run: Run,
    only: Iterable[str] | None = None,
    is_root: Callable[[], bool] = lambda: os.geteuid() == 0,
) -> dict[str, str]:
    services = select(settings, only)
    variables = settings["variables"]
    workdir: Path = settings["workdir"]
    if not variables.is_file():
        raise CreateError(f"variables file not found: {variables}")
    if is_root() and any(service in NO_ROOT for service in services):
        raise CreateError(
            f"run as a normal user: the hooks of {', '.join(NO_ROOT)} refuse root "
            "(they write the user's ids for files on the host)"
        )
    state = load_state(settings)
    existing = [s for s in services if s in state and (workdir / state[s]).exists()]
    if existing:
        raise CreateError(
            f"instances exist already: {', '.join(existing)}; "
            "remove their directories or choose another work directory"
        )
    workdir.mkdir(parents=True, exist_ok=True)

    for service in services:
        ref = settings.ref(service)
        run.overview(f"Creating {service} ({ref})")
        before = _directories(workdir)
        skips = [argument for path in SKIPS[service] for argument in ("--skip", path)]
        run.command(
            [
                sys.executable,
                "-m",
                "cruft",
                "create",
                "--no-input",
                "--checkout",
                ref,
                "--config-file",
                variables,
                "--output-dir",
                workdir,
                *skips,
                location(settings["templates.source"], service),
            ],  # fmt: skip
            cwd=workdir,
        )
        new = sorted(_directories(workdir) - before)
        if len(new) != 1:
            raise StepError(f"{service}: expected one new directory in {workdir}, found {new}")
        state[service] = new[0]
        _save_state(settings, state)
        run.note(f"{service}: {workdir / new[0]}")
        _restore_persistent(settings, service, workdir / new[0], run)

    _copy_wheels(settings, state, services, run)
    _certificates(settings, state, services, run)
    _secrets(settings, state, run)
    _wf_git_token(settings, state, run)
    return state


def _restore_persistent(settings: Settings, service: str, path: Path, run: Run) -> None:
    """Put files kept by "down --purge" back into a new instance."""
    for relative in PERSIST.get(service, ()):
        kept = settings["persist"] / service / relative
        if kept.is_file():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(kept, target)
            target.chmod(0o600)
            run.overview(f"Restored {service}/{relative} from {settings['persist']}")


def _directories(path: Path) -> set[str]:
    return {entry.name for entry in path.iterdir() if entry.is_dir() and entry.name != STATE_DIR}


def _copy_wheels(settings: Settings, state, services, run: Run) -> None:
    for package, source in settings["wheels"].items():
        service, pattern = WHEELS[package]
        if service not in services:
            continue
        wheels = sorted(Path(source).glob(pattern))
        if not wheels:
            raise CreateError(f"wheels.{package}: no {pattern} in {source}")
        target = settings["workdir"] / state[service] / "wheels"
        run.overview(f"Copying the {package} wheel into {service}")
        target.mkdir(exist_ok=True)
        for wheel in wheels:
            shutil.copy2(wheel, target / wheel.name)
            run.note(f"{wheel} -> {target}")


def _certificates(settings: Settings, state, services, run: Run) -> None:
    workdir: Path = settings["workdir"]
    mode = settings["tls"]
    if mode == "letsencrypt" or "proxy" not in state:
        return
    proxy = workdir / state["proxy"]
    ca = proxy / "certs" / "ca.crt"
    if mode == "own":
        run.overview(
            "TLS mode own: put the certificates into proxy/certs/ and the CA bundle "
            f"into certs/ of {', '.join(CA_USERS)}"
        )
        return
    if "proxy" in services:
        run.overview("Creating the local CA and the certificate of the proxy")
        run.command(["./adm_01_certs.sh"], cwd=proxy)
    if not ca.is_file():
        raise CreateError(f"local CA missing: {ca}")
    for service in CA_USERS:
        if service in services and service in state:
            target = workdir / state[service] / "certs"
            if not target.is_dir():
                raise CreateError(f"{service}: directory missing: {target}")
            shutil.copy2(ca, target / "ca.crt")
            run.note(f"{ca} -> {target}")
    run.overview(f"Local CA: {ca} (trust it in the browser or the operating system)")


def _secrets(settings: Settings, state, run: Run) -> None:
    if "keycloak" not in state:
        return
    workdir: Path = settings["workdir"]
    source = workdir / state["keycloak"] / ".env"
    for service, variable, kind, path, name in SECRET_COPIES:
        if service not in state:
            continue
        target = workdir / state[service] / path
        if kind == "file" and not target.exists():
            run.note(f"{service}/{path} does not exist: the instance does not use {variable}")
            continue
        value = copysecret.read_env(source, variable)
        if kind == "file":
            changed = copysecret.write_file(target, value)
        else:
            changed = copysecret.write_env(target, name, value, kestra=kind == "kestra")
        what = f"{variable} -> {service}/{path}" + (f" ({name})" if name else "")
        run.overview(f"Secret {what}" + ("" if changed else " (unchanged)"))


def _wf_git_token(settings: Settings, state, run: Run) -> None:
    """IFDD_META_WF_GIT_TOKEN (environment or .env) into wf as SECRET_GIT_TOKEN."""
    token = settings.secrets.get(WF_GIT_TOKEN)
    if "wf" not in state or not token:
        return
    target = settings["workdir"] / state["wf"] / "secrets" / "kestra.env"
    changed = copysecret.write_env(target, "GIT_TOKEN", token, kestra=True)
    run.overview(
        f"Secret {WF_GIT_TOKEN} -> wf/secrets/kestra.env (GIT_TOKEN)"
        + ("" if changed else " (unchanged)")
    )
