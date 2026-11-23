"""``ifdd-meta create``: the instances of an installation, ready to build.

Per service ``cruft create`` with the skip list and the chosen reference; then
optional wheels, the local CA (TLS mode ``local-ca``) and the client secrets
from ``keycloak/.env``. Existing instances are never overwritten. At the end
every instance becomes a local Git repository (no remote) with a first commit,
so that ``cruft update`` can merge later template changes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

import yaml

from . import copysecret, gitserver
from .catalogue import (
    CA_FILE_SETTINGS,
    CA_USERS,
    NO_ROOT,
    PERSIST,
    SECRET_COPIES,
    SERVICES,
    SKIPS,
    WHEELS,
)
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
    ordered = {key: state[key] for key in (*SERVICES, "git") if key in state}
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
    _require_git_identity(workdir)
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
    if settings["wf_git"] == "local":
        if "wf" in services:
            network = _variables(settings)["ifdd_proxy_network"]
            gitserver.prepare(workdir, settings["installation"], settings["git_host"], network, run)
            state["git"] = gitserver.DIRECTORY
            _save_state(settings, state)
    else:
        _wf_git_token(settings, state, run)
    for service in services:
        _commit(workdir / state[service], service, settings.ref(service), run)
    return state


#: Run git with the identity of the user's configuration only (no guessed address).
GIT = ("git", "-c", "user.useConfigOnly=true")
#: Files below secrets/ that templates keep in Git.
SECRET_PLACEHOLDERS = frozenset({"README.md", ".gitkeep"})


def _require_git_identity(directory: Path) -> None:
    """Every instance becomes a Git repository; its first commit needs name and e-mail."""
    directory = directory if directory.is_dir() else Path.cwd()
    for variable in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        result = subprocess.run(
            [*GIT, "var", variable], cwd=directory, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise CreateError(
                "Git needs a name and an e-mail address for the first commit of every "
                "instance: git config --global user.name '…'; "
                "git config --global user.email '…'"
            )


def secret_like(path: str) -> bool:
    """A tracked path that would put a secret into Git."""
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if name == ".env" or name.endswith((".key", ".pem", ".htpasswd")) or name == "acme.json":
        return True
    return (
        "secrets" in parts[:-1] and name not in SECRET_PLACEHOLDERS and not name.endswith(".dist")
    )


def _commit(path: Path, service: str, ref: str, run: Run) -> None:
    """Make the instance a local Git repository without a remote, for ``cruft update``.

    The templates' .gitignore keeps secrets, certificates and data out; if a
    secret-like file would be committed nevertheless, nothing is committed.
    """
    run.command([*GIT, "init", "-q", "-b", "main"], cwd=path)
    run.command([*GIT, "add", "--all"], cwd=path)
    listed = subprocess.run(
        [*GIT, "ls-files", "-z"], cwd=path, capture_output=True, text=True, check=True
    )
    leaks = sorted(name for name in listed.stdout.split("\0") if name and secret_like(name))
    if leaks:
        shutil.rmtree(path / ".git")
        raise CreateError(
            f"{service}: the .gitignore of the template would let these files into Git: "
            f"{', '.join(leaks)}; the instance stays without a Git repository"
        )
    message = f"Create {service} from its template at {ref}"
    run.command([*GIT, "commit", "-q", "-m", message], cwd=path)
    run.note(f"{service}: Git repository with the first commit")


def _variables(settings: Settings) -> dict:
    return yaml.safe_load(settings["variables"].read_text(encoding="utf-8"))["default_context"]


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
    if mode == "letsencrypt":
        _system_trust(settings, state, run)
        return
    if "proxy" not in state:
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


def _system_trust(settings: Settings, state, run: Run) -> None:
    """Let's Encrypt: no local CA, so settings that name its file must be empty."""
    for service, path, variable in CA_FILE_SETTINGS:
        if service not in state:
            continue
        target = settings["workdir"] / state[service] / path
        if copysecret.clear_env(target, variable):
            run.overview(f"{service}/{path}: {variable} emptied (Let's Encrypt: system trust)")


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
        elif kind == "new-file":
            # As the template's README asks (install -m 600); its readers run as root.
            changed = copysecret.write_file(target, value, new_mode=0o600)
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
