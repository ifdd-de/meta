"""Read files of the templates at a Git reference, without rendering them."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


class TemplateError(RuntimeError):
    """A template or one of its files cannot be read."""


def location(source: str, service: str) -> str:
    """Repository of a template: a directory below a local source, else a URL."""
    local = Path(source).expanduser()
    if local.is_dir():
        return str(local / service)
    return f"{source.rstrip('/')}/{service}"


def read_file(source: str, service: str, ref: str, name: str) -> str:
    """Content of ``name`` in the template of ``service`` at ``ref`` (committed state only)."""
    return at(location(source, service), ref, name, service)[1]


def at(repo: str, ref: str, name: str, what: str) -> tuple[str, str]:
    """(commit, content of ``name``) of a template repository at ``ref`` (tag, branch, sha)."""
    label = f"{what}@{ref}"
    if Path(repo).is_dir():
        commit = _git(["git", "-C", repo, "rev-parse", f"{ref}^{{commit}}"], label).strip()
        return commit, _git(["git", "-C", repo, "show", f"{commit}:{name}"], label)
    with tempfile.TemporaryDirectory(prefix="ifdd-meta-") as tmp:
        _git(["git", "init", "-q", tmp], what)
        _git(["git", "-C", tmp, "fetch", "-q", "--depth", "1", repo, ref], label)
        commit = _git(["git", "-C", tmp, "rev-parse", "FETCH_HEAD^{commit}"], label).strip()
        return commit, _git(["git", "-C", tmp, "show", f"{commit}:{name}"], label)


def cookiecutter_json(source: str, service: str, ref: str) -> dict:
    return parse_cookiecutter_json(
        read_file(source, service, ref, "cookiecutter.json"), service, ref
    )


def parse_cookiecutter_json(text: str, service: str, ref: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise TemplateError(
            f"{service}@{ref}: cookiecutter.json is not valid JSON: {error}"
        ) from None
    if not isinstance(data, dict):
        raise TemplateError(f"{service}@{ref}: cookiecutter.json is not an object")
    return data


def _git(command: list[str], what: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["no output"]
        raise TemplateError(f"{what}: {' '.join(command[:4])} failed: {detail[0]}")
    return result.stdout
