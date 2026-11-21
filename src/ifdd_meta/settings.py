"""Settings of ifdd-meta and where each value comes from.

Precedence, highest first: command line, environment variable, ``.env`` next to
the settings file, the settings file ``ifdd-meta.yaml``, built-in default.

Template variables are not settings; they live in the variables file that the
setting ``variables`` names.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

import yaml

from . import envfile

#: Services in the order of the catalogue (the start order is defined there).
SERVICES = (
    "proxy", "keycloak", "be", "ui", "schema", "type", "unit",
    "ct", "ts", "ts-api", "wf", "lab", "model",
)  # fmt: skip
#: Services an installation gets only when its description or settings name them.
OPTIONAL_SERVICES = ("lab",)
DEFAULT_SERVICES = tuple(s for s in SERVICES if s not in OPTIONAL_SERVICES)
ENVIRONMENTS = ("production", "development")
TLS_MODES = ("local-ca", "letsencrypt", "own")
WF_GIT_MODES = ("local", "external")
#: read: the integration tests only read; write: they also write test data.
TEST_MODES = ("read", "write")
WHEEL_PACKAGES = ("be-core", "ts-core")

SETTINGS_FILE = "ifdd-meta.yaml"
SETTINGS_ENV = "IFDD_META_SETTINGS"
ENV_PREFIX = "IFDD_META_"

COMMAND_LINE = "command line"
ENVIRONMENT = "environment"
DOTENV = ".env"
FILE = SETTINGS_FILE
DEFAULT = "default"


class SettingsError(ValueError):
    """The settings are missing, unreadable or invalid."""


@dataclass(frozen=True)
class Field:
    name: str  # dotted name, e.g. "templates.ref"
    kind: str  # str | path | choice | list | int | mapping
    default: Any
    choices: tuple[str, ...] = ()

    @property
    def env(self) -> str | None:
        """Name of the environment variable; mappings have none."""
        if self.kind == "mapping":
            return None
        return ENV_PREFIX + self.name.upper().replace(".", "_").replace("-", "_")


FIELDS = (
    Field("installation", "str", "example"),
    Field("variables", "path", "cookiecutter_all.yaml"),
    Field("environment", "choice", "production", ENVIRONMENTS),
    Field("tls", "choice", "local-ca", TLS_MODES),
    Field("templates.source", "str", "https://github.com/ifdd-de"),
    Field("templates.ref", "str", "main"),
    Field("templates.refs", "mapping", {}),
    Field("services", "list", list(DEFAULT_SERVICES), SERVICES),
    Field("workdir", "path", "work"),
    Field("persist", "path", "state"),
    Field("wheels", "mapping", {}),
    Field("wait_timeout", "int", 300),
    Field("wf_git", "choice", "external", WF_GIT_MODES),
    Field("git_host", "str", "git"),
    Field("tests", "choice", "read", TEST_MODES),
)
_BY_NAME = {field.name: field for field in FIELDS}


@dataclass(frozen=True)
class Settings:
    """Resolved settings: one value and its source per field."""

    file: Path | None
    values: Mapping[str, Any]
    sources: Mapping[str, str]
    #: IFDD_META_* values of .env and the environment (the environment wins), for
    #: secrets such as IFDD_META_WF_GIT_TOKEN; never shown.
    secrets: Mapping[str, str] = dataclass_field(default_factory=dict, repr=False)

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    def ref(self, service: str) -> str:
        """Git reference of the template of a service."""
        return self.values["templates.refs"].get(service, self.values["templates.ref"])

    def rows(self) -> list[tuple[str, str, str]]:
        """(name, value, source) for every field, for display."""
        return [
            (field.name, _display(self.values[field.name]), self.sources[field.name])
            for field in FIELDS
        ]


def load(
    settings_file: Path | None = None,
    cli: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Settings:
    """Resolve all settings.

    ``settings_file`` comes from the command line; without it the environment
    variable ``IFDD_META_SETTINGS`` and then ``./ifdd-meta.yaml`` are tried. A
    file named explicitly must exist; the default file may be missing.
    ``cli`` maps dotted field names to values given on the command line (None
    means not given).
    """
    cli = {key: value for key, value in (cli or {}).items() if value is not None}
    environ = environ if environ is not None else {}
    cwd = cwd or Path.cwd()
    unknown = set(cli) - set(_BY_NAME)
    if unknown:
        raise SettingsError(f"unknown settings on the command line: {', '.join(sorted(unknown))}")

    path = _settings_path(settings_file, environ, cwd)
    data = _read_settings_file(path) if path else {}
    base = path.parent if path else cwd
    dotenv = envfile.read(base / ".env")

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for field in FIELDS:
        raw, source, relative_to = _pick(field, cli, environ, dotenv, data, base, cwd)
        try:
            values[field.name] = _convert(field, raw, relative_to)
        except SettingsError as error:
            raise SettingsError(f"{field.name} ({source}): {error}") from None
        sources[field.name] = source
    for service in values["templates.refs"]:
        if service not in SERVICES:
            raise SettingsError(f"templates.refs ({FILE}): unknown service {service!r}")
    for package in values["wheels"]:
        if package not in WHEEL_PACKAGES:
            raise SettingsError(f"wheels ({FILE}): unknown package {package!r}")
    secrets = {
        key: value
        for key, value in {**dotenv, **environ}.items()
        if key.startswith(ENV_PREFIX) and value
    }
    return Settings(file=path, values=values, sources=sources, secrets=secrets)


def _settings_path(explicit: Path | None, environ: Mapping[str, str], cwd: Path) -> Path | None:
    if explicit is None and environ.get(SETTINGS_ENV):
        explicit = Path(environ[SETTINGS_ENV])
    if explicit is not None:
        path = explicit if explicit.is_absolute() else cwd / explicit
        if not path.is_file():
            raise SettingsError(f"settings file not found: {path}")
        return path
    default = cwd / SETTINGS_FILE
    return default if default.is_file() else None


def _read_settings_file(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise SettingsError(f"{path}: not valid YAML: {error}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SettingsError(f"{path}: expected a mapping at the top level")
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if key == "templates":
            if not isinstance(value, dict):
                raise SettingsError(f"{path}: templates must be a mapping")
            for sub, sub_value in value.items():
                flat[f"templates.{sub}"] = sub_value
        else:
            flat[str(key)] = value
    unknown = set(flat) - set(_BY_NAME)
    if unknown:
        raise SettingsError(f"{path}: unknown settings: {', '.join(sorted(unknown))}")
    return flat


def _pick(field, cli, environ, dotenv, data, base, cwd):
    """Return (raw value, source, directory relative paths refer to)."""
    if field.name in cli:
        return cli[field.name], COMMAND_LINE, cwd
    if field.env and field.env in environ:
        return environ[field.env], ENVIRONMENT, cwd
    if field.env and field.env in dotenv:
        return dotenv[field.env], DOTENV, base
    if field.name in data:
        return data[field.name], FILE, base
    return field.default, DEFAULT, base


def _convert(field: Field, raw: Any, relative_to: Path) -> Any:
    if field.kind == "str":
        if not isinstance(raw, str) or not raw.strip():
            raise SettingsError("expected a non-empty string")
        return raw.strip()
    if field.kind == "path":
        if not isinstance(raw, str | Path) or not str(raw).strip():
            raise SettingsError("expected a path")
        path = Path(str(raw).strip()).expanduser()
        return path if path.is_absolute() else (relative_to / path)
    if field.kind == "choice":
        if raw not in field.choices:
            raise SettingsError(f"expected one of {', '.join(field.choices)}, got {raw!r}")
        return raw
    if field.kind == "int":
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise SettingsError(f"expected a whole number, got {raw!r}") from None
        if isinstance(raw, bool) or number <= 0:
            raise SettingsError(f"expected a positive whole number, got {raw!r}")
        return number
    if field.kind == "list":
        items = [item.strip() for item in raw.split(",")] if isinstance(raw, str) else raw
        if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
            raise SettingsError("expected a list of names")
        unknown = [item for item in items if item not in field.choices]
        if unknown:
            raise SettingsError(f"unknown: {', '.join(unknown)}")
        if len(set(items)) != len(items):
            raise SettingsError("names must not repeat")
        return tuple(item for item in field.choices if item in items)
    if field.kind == "mapping":
        if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in raw.items()
        ):
            raise SettingsError("expected a mapping of names to non-empty strings")
        if field.name == "wheels":
            return {
                key: (p if (p := Path(value).expanduser()).is_absolute() else relative_to / p)
                for key, value in raw.items()
            }
        return dict(raw)
    raise AssertionError(field.kind)


def _display(value: Any) -> str:
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={val}" for key, val in value.items()) or "(none)"
    return str(value)
