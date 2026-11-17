"""``build``, ``up``, ``down``, ``status`` and ``logs`` of the created instances.

The commands use the paths an operator would use: ``docker compose`` with the
compose file of the environment, and the ``adm_*`` scripts where an instance
has them (ct, ts, wf).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import yaml

from .catalogue import PERSIST, SERVICES
from .create import STATE_DIR, CreateError, load_state, select
from .runner import Run
from .settings import Settings

#: Services that only pull upstream images when built.
PULL_ONLY = ("proxy", "keycloak", "schema", "type", "unit", "ts")

#: Start order (D4: Keycloak before be and ui). The proxy starts twice: Traefik
#: alone first, all its services (oauth2-proxy needs the realm) after the others.
START = (
    ("proxy", "traefik"),
    ("keycloak", "all"),
    ("be", "all"),
    ("ui", "all"),
    ("schema", "all"),
    ("type", "all"),
    ("unit", "all"),
    ("proxy", "all"),
    ("wf", "script"),
    ("ts", "script"),
    ("ts-api", "all"),
)
#: Compose service of Traefik in the proxy instance.
TRAEFIK = "proxy"


def compose(settings: Settings) -> list[str]:
    return ["docker", "compose", "-f", f"docker-compose-{settings['environment']}.yml"]


def _instances(settings: Settings, only: Iterable[str] | None) -> dict[str, Path]:
    state = load_state(settings)
    if not state:
        raise CreateError(f"no instances in {settings['workdir']}; run 'create' first")
    wanted = select(settings, only)
    return {
        service: settings["workdir"] / state[service]
        for service in SERVICES
        if service in wanted and service in state
    }


def build(settings: Settings, run: Run, only: Iterable[str] | None = None) -> None:
    environment = settings["environment"]
    for service, path in _instances(settings, only).items():
        if service == "model":
            continue
        run.overview(f"Building {service} ({environment})")
        if service in PULL_ONLY:
            run.command([*compose(settings), "pull"], cwd=path)
        if service == "ts":
            run.command(["./adm_01_plugins.sh"], cwd=path)
        elif service == "ct":
            run.command(["./adm_01_build.sh"], cwd=path)
        elif service == "wf":
            run.command(["./adm_01_build.sh", environment], cwd=path)
        elif service not in PULL_ONLY:
            run.command([*compose(settings), "build"], cwd=path)


def up(settings: Settings, run: Run, only: Iterable[str] | None = None) -> None:
    instances = _instances(settings, only)
    environment = settings["environment"]
    wait = ["--wait", "--wait-timeout", str(settings["wait_timeout"])]
    for network in _networks(settings, instances):
        if not _docker_ok(["docker", "network", "inspect", network]):
            run.overview(f"Creating network {network}")
            run.command(["docker", "network", "create", network], cwd=settings["workdir"])
    for service, phase in START:
        if service not in instances:
            continue
        path = instances[service]
        if service == "proxy" and phase == "traefik":
            run.overview(f"Starting proxy, Traefik only ({environment})")
            run.command([*compose(settings), "up", "--detach", *wait, TRAEFIK], cwd=path)
        elif service == "wf":
            run.overview(f"Starting wf ({environment})")
            run.command(["./adm_02_run.sh", environment], cwd=path)
        elif service == "ts":
            run.overview(f"Starting ts ({environment})")
            run.command(["./adm_02_run.sh", environment, *wait], cwd=path)
        else:
            label = "proxy, all services" if service == "proxy" else service
            run.overview(f"Starting {label} ({environment})")
            run.command([*compose(settings), "up", "--detach", *wait], cwd=path)


def down(
    settings: Settings, run: Run, only: Iterable[str] | None = None, purge: bool = False
) -> None:
    if purge and only:
        raise CreateError("--purge removes the whole installation; do not combine it with --only")
    instances = _instances(settings, only)
    order = list(dict.fromkeys(service for service, _ in reversed(START)))
    order += [service for service in instances if service not in order]
    for service in order:
        if service not in instances or service in ("ct", "model"):
            continue
        run.overview(f"Stopping {service}" + (" and removing its volumes" if purge else ""))
        command = [*compose(settings), "down", "--remove-orphans"]
        run.command(command + (["--volumes"] if purge else []), cwd=instances[service])
    if not purge:
        return
    for network in _networks(settings, instances):
        if _docker_ok(["docker", "network", "inspect", network]):
            run.overview(f"Removing network {network}")
            run.command(["docker", "network", "rm", network], cwd=settings["workdir"])
    _keep_persistent(settings, instances, run)
    for path in instances.values():
        run.overview(f"Deleting {path}")
        shutil.rmtree(path)
    shutil.rmtree(settings["workdir"] / STATE_DIR, ignore_errors=True)


def status(settings: Settings) -> list[tuple[str, str, str, str]]:
    """(service, container, state, health) of every container."""
    rows = []
    for service, path in _instances(settings, None).items():
        if service in ("ct", "model"):
            continue
        result = subprocess.run(
            [*compose(settings), "ps", "--all", "--format", "json"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        )
        containers = _parse_ps(result.stdout) if result.returncode == 0 else []
        if not containers:
            rows.append((service, "-", "not running" if result.returncode == 0 else "error", ""))
        for container in containers:
            rows.append(
                (
                    service,
                    str(container.get("Name", "?")),
                    str(container.get("State", "?")),
                    str(container.get("Health", "")),
                )
            )
    return rows


def logs(settings: Settings, run: Run, directory: Path) -> list[Path]:
    """Write the logs of every container of every instance into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for service, path in _instances(settings, None).items():
        if service in ("ct", "model"):
            continue
        target = directory / f"{service}.log"
        with target.open("w", encoding="utf-8") as handle:
            subprocess.run(
                [*compose(settings), "logs", "--no-color", "--timestamps"],
                cwd=path,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        written.append(target)
        run.note(f"{service}: {target}")
    return written


def _networks(settings: Settings, instances: dict[str, Path]) -> list[str]:
    context = yaml.safe_load(settings["variables"].read_text(encoding="utf-8"))["default_context"]
    networks = [context["ifdd_proxy_network"]]
    if "ts" in instances or "ts-api" in instances:
        networks.append(context["ifdd_ts_ingest_network"])
    return networks


def _keep_persistent(settings: Settings, instances: dict[str, Path], run: Run) -> None:
    for service, path in instances.items():
        for relative in PERSIST.get(service, ()):
            source = path / relative
            if source.is_file():
                target = settings["persist"] / service / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                target.chmod(0o600)
                run.overview(f"Kept {service}/{relative} in {settings['persist']}")


def _docker_ok(argv: list[str]) -> bool:
    result = subprocess.run(argv, capture_output=True, check=False)
    return result.returncode == 0


def _parse_ps(output: str) -> list[dict]:
    """``docker compose ps --format json`` prints an array or one object per line."""
    text = output.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]
