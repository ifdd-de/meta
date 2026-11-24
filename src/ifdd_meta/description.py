"""The short description of an installation that ``ifdd-meta compile`` reads."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .catalogue import DASHBOARD_LABEL, DEPENDENCIES, GIT_LABEL, HOST_LABELS, LOCAL_DEPENDENCIES
from .settings import DEFAULT_SERVICES, SERVICES, TLS_MODES, WF_GIT_MODES

_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_REALM = re.compile(r"[a-z0-9][a-z0-9_-]*")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_TOP = {
    "installation", "hosts", "tls", "contact", "realm", "extra_realms",
    "context_root", "services", "templates", "overrides", "wf_git", "ca_certs", "files",
}  # fmt: skip
_BLOCK_KEYS = {"host", "external", "ref"}
#: Targets of files: that keycloak/realm/build_realm.py turns into realm import files.
REALM_VALUES = "keycloak/realm/values_*.toml"
#: Targets that files: must not write: made by create (.env from the *.dist file,
#: with its secrets) or by cruft and Git.
_FORBIDDEN_TARGETS = {".env", ".cruft.json"}


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
    #: host label -> full name (host of a service block); others use the suffix.
    names: dict[str, str] = field(default_factory=dict)
    #: services that run on another server (external: true in their block)
    external: tuple[str, ...] = ()
    #: CA certificates (paths relative to the description) the services trust
    ca_certs: tuple[str, ...] = ()
    #: private files: (target "<service>/<path>", source relative to the description)
    files: tuple[tuple[str, str], ...] = ()

    def host(self, label: str) -> str:
        if label in self.names:
            return self.names[label]
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

    templates = data.get("templates") or {}
    if not isinstance(templates, dict) or set(templates) - {"ref", "refs"}:
        raise DescriptionError(f"{path}: templates takes only 'ref' and 'refs'")
    ref = templates.get("ref", "main")
    if not isinstance(ref, str) or not ref.strip():
        raise DescriptionError(f"{path}: templates.ref must be a non-empty text")

    raw_services = data.get("services", list(DEFAULT_SERVICES))
    if isinstance(raw_services, dict):
        if "refs" in templates:
            raise DescriptionError(
                f"{path}: with service blocks, put each ref into its block, not templates.refs"
            )
        services, external, names, refs = _blocks(path, raw_services)
    else:
        services, refs = _service_list(path, raw_services, templates.get("refs") or {})
        external, names = [], {}

    ca_certs = data.get("ca_certs") or []
    if not isinstance(ca_certs, list) or not all(isinstance(c, str) and c for c in ca_certs):
        raise DescriptionError(f"{path}: ca_certs must be a list of files")
    for cert in ca_certs:
        if not (path.parent / cert).is_file():
            raise DescriptionError(f"{path}: ca_certs: file not found: {cert}")
    if len({Path(c).stem for c in ca_certs}) != len(ca_certs):
        raise DescriptionError(f"{path}: ca_certs: file names must differ")

    wf_git = data.get("wf_git", "local")
    if wf_git not in WF_GIT_MODES:
        raise DescriptionError(
            f"{path}: wf_git must be one of {', '.join(WF_GIT_MODES)}: {wf_git!r}"
        )

    files = _files(path, data.get("files"), services)
    _check_realm_files(path, files, services, realm, extra)

    if tls == "letsencrypt" and not (suffix or names):
        raise DescriptionError(
            f"{path}: tls letsencrypt needs public names: hosts.suffix or a host per service"
        )
    _check_hosts(path, services, external, names, suffix, wf_git)
    _check_dependencies(path, services, external)

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
        refs={s: refs[s] for s in SERVICES if s in refs},
        names=names,
        external=tuple(s for s in SERVICES if s in external),
        ca_certs=tuple(ca_certs),
        files=tuple(files),
    )


def _service_list(path: Path, services: Any, raw_refs: Any) -> tuple[list[str], dict[str, str]]:
    """The short form: a list of local services, refs in templates.refs."""
    if not isinstance(services, list) or not services:
        raise DescriptionError(f"{path}: services must be a list or a mapping of service blocks")
    unknown = [s for s in services if s not in SERVICES]
    if unknown:
        raise DescriptionError(f"{path}: unknown services: {', '.join(map(str, unknown))}")
    if len(set(services)) != len(services):
        raise DescriptionError(f"{path}: services must not repeat")
    if not isinstance(raw_refs, dict):
        raise DescriptionError(f"{path}: templates.refs must be a mapping service: ref")
    refs = {}
    for service, service_ref in raw_refs.items():
        if service not in SERVICES:
            raise DescriptionError(f"{path}: templates.refs: unknown service {service!r}")
        if not isinstance(service_ref, str) or not service_ref.strip():
            raise DescriptionError(f"{path}: templates.refs.{service} must be a non-empty text")
        refs[service] = service_ref.strip()
    return services, refs


def _blocks(path: Path, raw: dict) -> tuple[list[str], list[str], dict[str, str], dict[str, str]]:
    """The block form: one block per service with host, external and ref."""
    services, external, names, refs = [], [], {}, {}
    for name, block in raw.items():
        where = f"{path}: services.{name}"
        block = {} if block is None else block
        if not isinstance(block, dict):
            raise DescriptionError(f"{where}: expected a block (host, external, ref) or {{}}")
        unknown = set(block) - _BLOCK_KEYS
        if unknown:
            raise DescriptionError(f"{where}: unknown keys: {', '.join(sorted(map(str, unknown)))}")
        host = block.get("host")
        if host is not None and (not isinstance(host, str) or not _is_domain(host)):
            raise DescriptionError(f"{where}.host must be a domain name: {host!r}")
        if name == GIT_LABEL:  # the local Git server of wf: a host, nothing else
            if set(block) - {"host"} or host is None:
                raise DescriptionError(f"{where}: the Git server of wf takes only a host")
            names[GIT_LABEL] = host
            continue
        if name not in SERVICES:
            raise DescriptionError(f"{where}: unknown service")
        is_external = block.get("external", False)
        if not isinstance(is_external, bool):
            raise DescriptionError(f"{where}.external must be true or false")
        if is_external and name not in HOST_LABELS:
            raise DescriptionError(f"{where}: only a service with a host can be external")
        if "ref" in block:
            if not isinstance(block["ref"], str) or not block["ref"].strip():
                raise DescriptionError(f"{where}.ref must be a non-empty text")
            refs[name] = block["ref"].strip()
        if host is not None:
            if name not in HOST_LABELS and name != "proxy":
                raise DescriptionError(f"{where}: {name} has no host of its own")
            names[HOST_LABELS.get(name, DASHBOARD_LABEL)] = host
        (external if is_external else services).append(name)
    if not services:
        raise DescriptionError(f"{path}: services: no local service")
    return services, external, names, refs


def _files(path: Path, raw: Any, services: list[str]) -> list[tuple[str, str]]:
    """files: a list of {from, to}; from relative to the description, to below a local service."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DescriptionError(f"{path}: files must be a list of {{from: …, to: …}}")
    files = []
    for number, entry in enumerate(raw, start=1):
        where = f"{path}: files, entry {number}"
        if not isinstance(entry, dict) or set(entry) != {"from", "to"}:
            raise DescriptionError(f"{where}: expected exactly the keys from and to")
        source, target = entry["from"], entry["to"]
        if not all(isinstance(value, str) and value.strip() for value in (source, target)):
            raise DescriptionError(f"{where}: from and to must be non-empty texts")
        if not (path.parent / source).is_file():
            raise DescriptionError(f"{where}: from: file not found: {source}")
        parts = PurePosixPath(target).parts
        if (
            target != target.strip()
            or "\\" in target
            or PurePosixPath(target).is_absolute()
            or len(parts) < 2
            or any(part in ("..", ".") for part in parts)
            or str(PurePosixPath(*parts)) != target
        ):
            raise DescriptionError(
                f"{where}: to must be <service>/<path inside the instance>: {target!r}"
            )
        if parts[0] not in services:
            raise DescriptionError(
                f"{where}: to: {parts[0]!r} is not a local service of this installation"
            )
        if ".git" in parts[1:] or (len(parts) == 2 and parts[-1] in _FORBIDDEN_TARGETS):
            raise DescriptionError(f"{where}: to: {target} is written by create, cruft or Git")
        files.append((target, source))
    targets = [target for target, _ in files]
    if len(set(targets)) != len(targets):
        raise DescriptionError(f"{path}: files: a target appears twice")
    return files


def realm_files(files: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> list[str]:
    """The targets of files: that build_realm.py reads (keycloak/realm/values_*.toml)."""
    return [
        target
        for target, _ in files
        if fnmatch.fnmatchcase(target, REALM_VALUES) and target.count("/") == 2
    ]


def _check_realm_files(path, files, services, realm, extra) -> None:
    """With a local Keycloak: one values file per further realm, and none for another realm."""
    if "keycloak" not in services:
        return
    sources = dict(files)
    found: dict[str, str] = {}
    for target in realm_files(files):
        source = path.parent / sources[target]
        try:
            name = tomllib.loads(source.read_text(encoding="utf-8"))["realm"]["name"]
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
            raise DescriptionError(
                f"{path}: files: {sources[target]}: no realm name ([realm] name): {error}"
            ) from None
        if not isinstance(name, str) or name not in extra:
            where = "the realm of the installation" if name == realm else "not in extra_realms"
            raise DescriptionError(
                f"{path}: files: {sources[target]} is for realm {name!r}, {where}; "
                "values files are for the further realms (extra_realms) only"
            )
        if name in found:
            raise DescriptionError(
                f"{path}: files: realm {name!r} has two values files "
                f"({found[name]}, {sources[target]})"
            )
        found[name] = sources[target]
    missing = [name for name in extra if name not in found]
    if missing:
        raise DescriptionError(
            f"{path}: extra_realms {', '.join(missing)} without a values file: add to files "
            "{from: <file>, to: keycloak/realm/values_<name>.toml}; Keycloak creates a realm "
            "only from its import file"
        )


def _check_hosts(path, services, external, names, suffix, wf_git) -> None:
    """Without hosts.suffix every service with a host needs a host in its block."""
    if not names or suffix:
        return
    missing = [s for s in SERVICES if s in [*services, *external] and s in HOST_LABELS
               and HOST_LABELS[s] not in names]  # fmt: skip
    if "wf" in services and wf_git == "local" and GIT_LABEL not in names:
        missing.append("git")
    if missing:
        raise DescriptionError(
            f"{path}: no host for {', '.join(missing)}: give each a host in its block "
            "or set hosts.suffix"
        )


def _check_dependencies(path, services, external) -> None:
    present = {*services, *external}
    for service in services:
        lacking = [d for d in DEPENDENCIES.get(service, ()) if d not in present]
        if lacking:
            raise DescriptionError(
                f"{path}: {service} needs {', '.join(lacking)}: add it as a local service "
                "or as external: true"
            )
        not_local = [d for d in LOCAL_DEPENDENCIES.get(service, ()) if d not in services]
        if not_local:
            raise DescriptionError(
                f"{path}: {service} needs {', '.join(not_local)} on the same server"
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
