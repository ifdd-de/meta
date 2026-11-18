"""The short description of an installation that ``ifdd-meta compile`` reads."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .settings import SERVICES, TLS_MODES, WF_GIT_MODES

_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_REALM = re.compile(r"[a-z0-9][a-z0-9_-]*")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_TOP = {
    "installation", "hosts", "tls", "contact", "realm", "extra_realms",
    "context_root", "services", "templates", "overrides", "wf_git",
}  # fmt: skip


class DescriptionError(ValueError):
    """The description is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Description:
    path: Path
    installation: str
    suffix: str  # empty: short host names
    tls: str
    contact: str
    realm: str
    extra_realms: tuple[str, ...]
    context_root: str
    services: tuple[str, ...]
    ref: str
    overrides: dict[str, str | list[str]] = field(default_factory=dict)
    wf_git: str = "local"
    #: service -> Git reference, for templates without a tag of ``ref``.
    refs: dict[str, str] = field(default_factory=dict)

    def host(self, label: str) -> str:
        return f"{label}.{self.suffix}" if self.suffix else label


def load(path: Path) -> Description:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DescriptionError(f"{path}: {error.strerror}") from None
    except yaml.YAMLError as error:
        raise DescriptionError(f"{path}: not valid YAML: {error}") from None
    if not isinstance(data, dict):
        raise DescriptionError(f"{path}: expected a mapping at the top level")
    unknown = set(data) - _TOP
    if unknown:
        raise DescriptionError(f"{path}: unknown keys: {', '.join(sorted(unknown))}")

    def text(key: str, default: str | None = None) -> str:
        value = data.get(key, default)
        if not isinstance(value, str) or not value.strip():
            raise DescriptionError(f"{path}: {key} must be a non-empty text")
        return value.strip()

    installation = text("installation")
    if not _LABEL.fullmatch(installation):
        raise DescriptionError(f"{path}: installation must be a lower-case name: {installation!r}")

    hosts = data.get("hosts") or {}
    if not isinstance(hosts, dict) or set(hosts) - {"suffix"}:
        raise DescriptionError(f"{path}: hosts takes only 'suffix'")
    suffix = hosts.get("suffix") or ""
    if not isinstance(suffix, str) or (suffix and not _is_domain(suffix)):
        raise DescriptionError(f"{path}: hosts.suffix must be a domain name or empty: {suffix!r}")

    tls = data.get("tls", "local-ca")
    if tls not in TLS_MODES:
        raise DescriptionError(f"{path}: tls must be one of {', '.join(TLS_MODES)}: {tls!r}")
    contact = text("contact", "admin@example.org")
    if not _EMAIL.fullmatch(contact):
        raise DescriptionError(f"{path}: contact must be an e-mail address: {contact!r}")
    if tls == "letsencrypt" and not suffix:
        raise DescriptionError(f"{path}: tls letsencrypt needs hosts.suffix (public names)")

    realm = text("realm", "ifdd")
    extra = data.get("extra_realms") or []
    if not isinstance(extra, list) or not all(isinstance(r, str) for r in extra):
        raise DescriptionError(f"{path}: extra_realms must be a list of names")
    for name in [realm, *extra]:
        if not _REALM.fullmatch(name) or name == "master":
            raise DescriptionError(f"{path}: invalid realm name {name!r}")
    if len({realm, *extra}) != len(extra) + 1:
        raise DescriptionError(f"{path}: realm names must not repeat")

    context_root = text("context_root", "ifdd.de")
    if not _is_domain(context_root) or context_root != context_root.lower():
        raise DescriptionError(f"{path}: context_root must be a lower-case dotted name")

    services = data.get("services", list(SERVICES))
    if not isinstance(services, list) or not services:
        raise DescriptionError(f"{path}: services must be a list")
    unknown_services = [s for s in services if s not in SERVICES]
    if unknown_services:
        raise DescriptionError(f"{path}: unknown services: {', '.join(map(str, unknown_services))}")
    if len(set(services)) != len(services):
        raise DescriptionError(f"{path}: services must not repeat")

    templates = data.get("templates") or {}
    if not isinstance(templates, dict) or set(templates) - {"ref", "refs"}:
        raise DescriptionError(f"{path}: templates takes only 'ref' and 'refs'")
    ref = templates.get("ref", "main")
    if not isinstance(ref, str) or not ref.strip():
        raise DescriptionError(f"{path}: templates.ref must be a non-empty text")
    refs = templates.get("refs") or {}
    if not isinstance(refs, dict):
        raise DescriptionError(f"{path}: templates.refs must be a mapping service: ref")
    for service, service_ref in refs.items():
        if service not in SERVICES:
            raise DescriptionError(f"{path}: templates.refs: unknown service {service!r}")
        if not isinstance(service_ref, str) or not service_ref.strip():
            raise DescriptionError(f"{path}: templates.refs.{service} must be a non-empty text")

    wf_git = data.get("wf_git", "local")
    if wf_git not in WF_GIT_MODES:
        raise DescriptionError(
            f"{path}: wf_git must be one of {', '.join(WF_GIT_MODES)}: {wf_git!r}"
        )

    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise DescriptionError(f"{path}: overrides must be a mapping")
    clean: dict[str, str | list[str]] = {}
    for key, value in overrides.items():
        clean[str(key)] = _override_value(path, str(key), value)

    return Description(
        path=path,
        installation=installation,
        suffix=suffix,
        tls=tls,
        contact=contact,
        realm=realm,
        extra_realms=tuple(extra),
        context_root=context_root,
        services=tuple(s for s in SERVICES if s in services),
        ref=ref.strip(),
        overrides=clean,
        wf_git=wf_git,
        refs={s: refs[s].strip() for s in SERVICES if s in refs},
    )


def _is_domain(name: str) -> bool:
    labels = name.split(".")
    return len(labels) >= 2 and all(_LABEL.fullmatch(label) for label in labels)


def _override_value(path: Path, key: str, value: Any) -> str | list[str]:
    if isinstance(value, bool):
        raise DescriptionError(f'{path}: overrides.{key}: write yes/no values in quotes, e.g. "y"')
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return list(value)
    raise DescriptionError(f"{path}: overrides.{key}: expected a text or a list of texts")
