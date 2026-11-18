"""``ifdd-meta update``: later template changes into the existing instances.

Per instance ``cruft update`` to the reference of the settings, then one Git
commit. The result should equal a fresh ``create`` at that reference:

- the values of the variables file (``cookiecutter_all.yaml``) are passed again,
  so the description stays the only source of the shared values;
- a variable the template owns (not in the variables file) gets the template's
  new default if that default changed (for example ``ifdd_ui_version``);
  cruft alone keeps the value recorded in ``.cruft.json``;
- private variables (``_…``) are read from the template again;
- the skip list in ``.cruft.json`` stays as ``create`` wrote it (cruft 2.16.0
  appends the ``[tool.cruft]`` skip list of ``pyproject.toml`` to it on every
  update);
- ``.env`` and other files made from a ``*.dist`` file are on the skip list
  (secrets). A setting whose value is still the old default of the ``*.dist``
  file gets the new default; values changed by hand and secrets stay. New or
  removed settings are shown, not added.

An instance needs its Git repository from ``create`` and a clean working tree.
cruft merges the template changes with ``git apply -3``; at the first conflict
or rejected hunk ``update`` stops and leaves that instance uncommitted, the
following ones untouched. Nothing is built or started: ``build``, ``up`` and
``test`` come afterwards. ``--check`` only shows what would change.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import copysecret, templates
from .catalogue import SERVICES
from .create import GIT, CreateError, load_state, secret_like, select
from .runner import Run
from .settings import Settings

#: A line ``NAME=value`` of a .env file (``export`` allowed).
ENV_LINE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)=(.*?)(\r?\n)?$")


class UpdateError(CreateError):
    """An instance cannot be updated."""


@dataclass
class Plan:
    """What updating one instance means."""

    service: str
    path: Path
    template: str
    ref: str
    old: str
    new: str
    variables: dict = field(default_factory=dict)
    changed_defaults: list[str] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        return self.old != self.new


def variables_to_pass(shared: dict, old_json: dict, new_json: dict) -> tuple[dict, list[str]]:
    """Values for ``cruft update`` and the names of template-owned defaults that changed.

    ``shared`` are the values of the variables file. Only names the new
    template knows are passed; private names come from the template itself.
    """
    values: dict = {}
    changed: list[str] = []
    for name, default in new_json.items():
        if name.startswith("_"):
            continue
        if name in shared:
            values[name] = shared[name]
        elif name in old_json and old_json[name] != default:
            values[name] = default
            changed.append(name)
    return values, changed


def plan(settings: Settings, only: Iterable[str] | None = None) -> list[Plan]:
    try:
        return _plan(settings, only)
    except templates.TemplateError as error:
        raise UpdateError(str(error)) from None


def _plan(settings: Settings, only: Iterable[str] | None) -> list[Plan]:
    state = load_state(settings)
    if not state:
        raise UpdateError(f"no instances in {settings['workdir']}; run 'create' first")
    shared = yaml.safe_load(settings["variables"].read_text(encoding="utf-8"))["default_context"]
    wanted = select(settings, only)
    plans = []
    for service in SERVICES:
        if service not in wanted or service not in state:
            continue
        path = settings["workdir"] / state[service]
        cruft = json.loads((path / ".cruft.json").read_text(encoding="utf-8"))
        repo, ref = cruft["template"], settings.ref(service)
        new, new_text = templates.at(repo, ref, "cookiecutter.json", service)
        entry = Plan(service, path, repo, ref, cruft["commit"], new)
        if entry.needed:
            _, old_text = templates.at(repo, cruft["commit"], "cookiecutter.json", service)
            old_json = templates.parse_cookiecutter_json(old_text, service, cruft["commit"])
            new_json = templates.parse_cookiecutter_json(new_text, service, ref)
            entry.variables, entry.changed_defaults = variables_to_pass(shared, old_json, new_json)
        plans.append(entry)
    return plans


def check(settings: Settings, run: Run, only: Iterable[str] | None = None) -> list[Plan]:
    plans = plan(settings, only)
    for entry in plans:
        if not entry.needed:
            run.overview(f"{entry.service}: up to date ({entry.ref})")
            continue
        detail = (
            f"; new defaults: {', '.join(entry.changed_defaults)}" if entry.changed_defaults else ""
        )
        step = f"{entry.old[:7]} -> {entry.new[:7]} ({entry.ref})"
        run.overview(f"{entry.service}: would update {step}{detail}")
    return plans


def update(settings: Settings, run: Run, only: Iterable[str] | None = None) -> list[str]:
    """Update every instance that is behind; returns the updated services."""
    plans = plan(settings, only)
    for entry in plans:  # all preconditions first: nothing changes if one fails
        if entry.needed:
            _require_clean_repository(entry)
    updated = []
    for entry in plans:
        if not entry.needed:
            run.overview(f"{entry.service}: up to date ({entry.ref})")
            continue
        run.overview(f"Updating {entry.service} {entry.old[:7]} -> {entry.new[:7]} ({entry.ref})")
        if entry.changed_defaults:
            run.overview(f"{entry.service}: new defaults of {', '.join(entry.changed_defaults)}")
        skip = json.loads((entry.path / ".cruft.json").read_text(encoding="utf-8"))["skip"]
        _cruft_update(entry, run)
        _stop_at_conflicts(entry)
        _restore_skip(entry.path / ".cruft.json", skip)
        if _commit(entry, run):
            updated.append(entry.service)
            _merge_dist_files(entry, run)
    return updated


def _restore_skip(cruft_file: Path, skip: list) -> None:
    """Undo the growing skip list of cruft 2.16.0 (written as cruft writes it)."""
    state = json.loads(cruft_file.read_text(encoding="utf-8"))
    if state.get("skip") != skip:
        state["skip"] = skip
        text = json.dumps(state, ensure_ascii=False, indent=2, separators=(",", ": "))
        cruft_file.write_text(text + "\n", encoding="utf-8")


def target_of(dist: str) -> str:
    """The file a ``*.dist`` file becomes: dotenv.dist -> .env, x.env.dist -> x.env."""
    path = Path(dist)
    name = ".env" if path.name == "dotenv.dist" else path.name.removesuffix(".dist")
    return str(path.with_name(name))


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        match = ENV_LINE.match(line)
        if match:
            values[match.group(2)] = match.group(3)
    return values


def merge_env(
    current: str, old_dist: str, new_dist: str
) -> tuple[str, list[str], list[str], list[str]]:
    """(text, changed, new, removed): new defaults for values that are still the old ones."""
    old, new = parse_env(old_dist), parse_env(new_dist)
    present = parse_env(current)
    changed = [
        name
        for name, value in new.items()
        if name in old and old[name] != value and present.get(name) == old[name]
    ]
    lines = current.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = ENV_LINE.match(line)
        if match and match.group(2) in changed:
            prefix, name, _, end = match.groups()
            lines[index] = f"{prefix}{name}={new[name]}{end or ''}"
    added = [name for name in new if name not in old and name not in present]
    removed = [name for name in old if name not in new and name in present]
    return "".join(lines), changed, added, removed


def _merge_dist_files(entry: Plan, run: Run) -> None:
    names = _git(entry.path, "diff", "--name-only", "HEAD~1", "HEAD").splitlines()
    for dist in (name for name in names if name.endswith(".dist")):
        target = entry.path / target_of(dist)
        if not target.is_file():
            continue
        old = _git_show(entry.path, f"HEAD~1:{dist}")
        new = _git_show(entry.path, f"HEAD:{dist}")
        text, changed, added, removed = merge_env(target.read_text(encoding="utf-8"), old, new)
        relative = target.relative_to(entry.path)
        if changed:
            copysecret.replace_file(target, text)
            run.overview(f"{entry.service}: {relative}: new defaults of {', '.join(changed)}")
        if added:
            run.overview(
                f"{entry.service}: {relative}: new settings in {dist}: {', '.join(added)}; "
                "add them by hand"
            )
        if removed:
            run.overview(f"{entry.service}: {relative}: no longer in {dist}: {', '.join(removed)}")


def _git_show(path: Path, spec: str) -> str:
    """Content of ``<rev>:<file>``; empty if the file did not exist there."""
    result = subprocess.run([*GIT, "show", spec], cwd=path, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _require_clean_repository(entry: Plan) -> None:
    if not (entry.path / ".git").is_dir():
        raise UpdateError(
            f"{entry.service}: {entry.path} is no Git repository; 'update' needs the "
            "repository that 'create' makes (git init, git add --all, git commit)"
        )
    status = _git(entry.path, "status", "--porcelain")
    if status.strip():
        raise UpdateError(
            f"{entry.service}: uncommitted changes in {entry.path}; commit or discard them first"
        )


def _cruft_update(entry: Plan, run: Run) -> None:
    with tempfile.TemporaryDirectory(prefix="ifdd-meta-update-") as tmp:
        variables = Path(tmp) / "variables.json"
        variables.write_text(
            json.dumps({"context": {"cookiecutter": entry.variables}}), encoding="utf-8"
        )
        run.command(
            [
                sys.executable,
                "-m",
                "cruft",
                "update",
                "--skip-apply-ask",
                "--checkout",
                entry.ref,
                "--refresh-private-variables",
                "--variables-to-update-file",
                variables,
            ],  # fmt: skip
            cwd=entry.path,
        )


def _stop_at_conflicts(entry: Plan) -> None:
    status = _git(entry.path, "status", "--porcelain", "--untracked-files=all")
    lines = status.splitlines()
    unmerged = [
        line[3:] for line in lines if line[:2] in {"UU", "AA", "DU", "UD", "AU", "UA", "DD"}
    ]
    rejected = [line[3:] for line in lines if line.startswith("??") and line.endswith(".rej")]
    if unmerged or rejected:
        found = ", ".join(unmerged + rejected)
        raise UpdateError(
            f"{entry.service}: conflicts in {found}; resolve them in {entry.path}, commit, and "
            "run 'update' again for the remaining instances (nothing else was changed)"
        )


def _commit(entry: Plan, run: Run) -> bool:
    _git(entry.path, "add", "--all")
    leaks = [name for name in _git(entry.path, "diff", "--cached", "--name-only").splitlines()
             if secret_like(name)]  # fmt: skip
    if leaks:
        _git(entry.path, "reset", "-q")
        raise UpdateError(
            f"{entry.service}: the update would put secret-like files into Git: "
            f"{', '.join(leaks)}; nothing committed"
        )
    changed = _git(entry.path, "diff", "--cached", "--name-only").splitlines()
    if not changed:
        run.overview(f"{entry.service}: no file changed")
        return False
    _git(entry.path, "commit", "-q", "-m", f"Update {entry.service} to {entry.ref}")
    run.overview(f"{entry.service}: committed ({len(changed)} files)")
    return True


def _git(path: Path, *argv: str) -> str:
    result = subprocess.run([*GIT, *argv], cwd=path, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
        raise UpdateError(f"git {' '.join(argv[:2])} in {path}: {detail[0]}")
    return result.stdout
