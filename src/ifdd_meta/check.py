"""``ifdd-meta config check``: the variables file against the templates.

Read-only. Rule B.3 makes a missing template variable an error; a variable no
template defines is an error as well, because cookiecutter would ignore it
silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from .catalogue import SECRET_ENDINGS, SERVICES, VERSION_VARIABLES
from .settings import Settings
from .templates import TemplateError, cookiecutter_json

_REFERENCE = re.compile(r"cookiecutter\.([a-z0-9_]+)")


@dataclass(frozen=True)
class Finding:
    message: str


@dataclass(frozen=True)
class Report:
    findings: list[Finding]
    variables: int
    templates: int


def run(settings: Settings, fetch=cookiecutter_json) -> Report:
    findings: list[Finding] = []
    path = settings["variables"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        return Report([Finding(f"variables file {path}: {error.strerror}")], 0, 0)
    except yaml.YAMLError as error:
        return Report([Finding(f"variables file {path}: not valid YAML: {error}")], 0, 0)
    context = data.get("default_context") if isinstance(data, dict) else None
    if not isinstance(context, dict):
        return Report([Finding(f"variables file {path}: no mapping 'default_context'")], 0, 0)

    defined: dict[str, tuple[str, object]] = {}  # variable -> (first template, default)
    per_service: dict[str, set[str]] = {}
    for service in SERVICES:
        ref = settings.ref(service)
        try:
            template = fetch(settings["templates.source"], service, ref)
        except TemplateError as error:
            findings.append(Finding(str(error)))
            continue
        names = {key for key in template if not key.startswith("_")}
        per_service[service] = names
        for name in names:
            defined.setdefault(name, (service, template[name]))
    if len(per_service) < len(SERVICES):
        return Report(findings, len(context), len(per_service))

    for service in settings["services"]:
        for name in sorted(per_service[service] - set(context) - VERSION_VARIABLES):
            findings.append(Finding(f"{name} (template {service}) is missing"))
    for name in sorted(set(context) - set(defined)):
        findings.append(Finding(f"{name} is defined by no template; cookiecutter would ignore it"))
    for name in sorted(set(context) & VERSION_VARIABLES):
        findings.append(Finding(f"{name} must not be set; versions come from the template"))
    for name in sorted(context):
        if name.endswith(SECRET_ENDINGS):
            findings.append(Finding(f"{name} looks like a secret; secrets belong in .env"))
        value = context[name]
        if name in defined:
            findings.extend(_check_value(name, value, defined[name][1]))
    return Report(findings, len(context), len(per_service))


def _check_value(name: str, value: object, default: object) -> list[Finding]:
    if isinstance(default, list):
        if not isinstance(value, list) or sorted(map(str, value)) != sorted(map(str, default)):
            return [
                Finding(
                    f"{name} is a choice: list {', '.join(map(str, default))} "
                    "in any order, the selected entry first"
                )
            ]
        return []
    if isinstance(value, list | dict) or value is None or isinstance(value, bool):
        return [Finding(f"{name} must be a text in quotes")]
    text = str(value)
    if "{{" in text or "{%" in text:
        own = name.removesuffix("_prj_slug") + "_prj_name"
        references = set(_REFERENCE.findall(text))
        if not name.endswith("_prj_slug") or references - {own}:
            return [Finding(f"{name} uses Jinja; only a slug may refer to its own {own}")]
    return []
