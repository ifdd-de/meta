"""``build``, ``up``, ``down``, ``status`` and ``logs`` of the created instances.

The commands use the paths an operator would use: ``docker compose`` with the
compose file of the environment, and the ``adm_*`` scripts where an instance
has them (ct, ts, wf, lab).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import yaml

from . import gitserver
from .catalogue import PERSIST, PERSIST_VOLUMES, SERVICES
from .create import STATE_DIR, STATE_FILE, CreateError, load_state, select
from .runner import Run
from .settings import Settings

#: Services that only pull upstream images when built (model: nginx; its builder
#: `just build` is not run here).
PULL_ONLY = ("proxy", "keycloak", "schema", "type", "unit", "ts", "model")

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
    ("model", "all"),
    ("proxy", "all"),
    ("git", "git"),
    ("wf", "script"),
    ("ts", "script"),
    ("ts-api", "all"),
    # lab last: its hub needs Keycloak (login) and be (the notebooks' data).
    ("lab", "script"),
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
    instances = {
        service: settings["workdir"] / state[service]
        for service in SERVICES
        if service in wanted and service in state
    }
    if "wf" in instances and "git" in state and settings["wf_git"] == "local":
        instances["git"] = settings["workdir"] / state["git"]
    return instances


def _compose_for(settings: Settings, service: str) -> list[str]:
    """The Git server has one compose file; the instances one per environment."""
    return ["docker", "compose"] if service == "git" else compose(settings)


def build(settings: Settings, run: Run, only: Iterable[str] | None = None) -> None:
    environment = settings["environment"]
    for service, path in _instances(settings, only).items():
        if service == "git":
            run.overview("Pulling the image of the Git server for wf")
            run.command(["docker", "compose", "pull"], cwd=path)
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
        elif service == "lab":
            run.command(["./adm_01_build.sh"], cwd=path)
        elif service not in PULL_ONLY:
            run.command([*compose(settings), "build"], cwd=path)


def up(settings: Settings, run: Run, only: Iterable[str] | None = None) -> None:
    instances = _instances(settings, only)
    environment = settings["environment"]
    wait = ["--wait", "--wait-timeout", str(settings["wait_timeout"])]
    check_mounts(settings, run)
    for network, internal in _networks(settings, instances):
        if not _docker_ok(["docker", "network", "inspect", network]):
            run.overview(f"Creating network {network}" + (" (internal)" if internal else ""))
            create_net = ["docker", "network", "create", *(["--internal"] if internal else [])]
            run.command([*create_net, network], cwd=settings["workdir"])
    for service, phase in START:
        if service not in instances:
            continue
        path = instances[service]
        if service == "proxy" and phase == "traefik":
            run.overview(f"Starting proxy, Traefik only ({environment})")
            run.command([*compose(settings), "up", "--detach", *wait, TRAEFIK], cwd=path)
        elif service == "git":
            run.overview("Starting the Git server for wf")
            run.command(["docker", "compose", "up", "--detach", *wait], cwd=path)
            gitserver.provision(
                path,
                ["docker", "compose"],
                _context(settings),
                instances["wf"] / "secrets" / "kestra.env",
                run,
            )
        elif service == "wf":
            run.overview(f"Starting wf ({environment})")
            run.command(["./adm_02_run.sh", environment], cwd=path)
        elif service == "ts":
            run.overview(f"Starting ts ({environment})")
            run.command(["./adm_02_run.sh", environment, *wait], cwd=path)
        elif service == "lab":
            # Production only (the template has no development compose file);
            # the script loads the user images into Docker-in-Docker and waits.
            run.overview("Starting lab")
            run.command(["./adm_02_run.sh"], cwd=path)
        else:
            label = "proxy, all services" if service == "proxy" else service
            run.overview(f"Starting {label} ({environment})")
            run.command([*compose(settings), "up", "--detach", *wait], cwd=path)


def down(
    settings: Settings, run: Run, only: Iterable[str] | None = None, purge: bool = False
) -> None:
    if purge and only:
        raise CreateError("--purge removes the whole installation; do not combine it with --only")
    if purge:
        refuse_if_protected(settings, "down --purge")
    instances = _instances(settings, only)
    order = list(dict.fromkeys(service for service, _ in reversed(START)))
    order += [service for service in instances if service not in order]
    for service in order:
        if service not in instances or service == "ct":
            continue
        keep = PERSIST_VOLUMES.get(service, ()) if purge else ()
        run.overview(f"Stopping {service}" + (" and removing its volumes" if purge else ""))
        command = [*_compose_for(settings, service), "down", "--remove-orphans"]
        if purge and not keep:
            command.append("--volumes")
        run.command(command, cwd=instances[service])
        if keep:
            _remove_volumes_except(settings, service, instances[service], keep, run)
    if not purge:
        return
    for network, _ in _networks(settings, instances):
        if _docker_ok(["docker", "network", "inspect", network]):
            run.overview(f"Removing network {network}")
            run.command(["docker", "network", "rm", network], cwd=settings["workdir"])
    _keep_persistent(settings, instances, run)
    for path in instances.values():
        run.overview(f"Deleting {path}")
        shutil.rmtree(path)
    shutil.rmtree(settings["workdir"] / STATE_DIR, ignore_errors=True)


def status(
    settings: Settings, only: Iterable[str] | None = None
) -> list[tuple[str, str, str, str]]:
    """(service, container, state, health) of every container."""
    rows = []
    for service, path in _instances(settings, only).items():
        if service == "ct":
            continue
        result = subprocess.run(
            [*_compose_for(settings, service), "ps", "--all", "--format", "json"],
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
        if service == "ct":
            continue
        target = directory / f"{service}.log"
        with target.open("w", encoding="utf-8") as handle:
            subprocess.run(
                [*_compose_for(settings, service), "logs", "--no-color", "--timestamps"],
                cwd=path,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        written.append(target)
        run.note(f"{service}: {target}")
    return written


#: Exit code of the check command when the file is missing (docker itself uses 125-127).
NOT_SEEN = 1


def check_mounts(settings: Settings, run: Run) -> None:
    """Whether the Docker daemon sees the work directory, before anything starts.

    A daemon in a VM (Colima, Docker Desktop) sees only the shared directories.
    For a bind mount whose source it does not see, Docker creates an empty
    directory, and the services fail later with "is a directory" or stay
    unhealthy. The check mounts the state directory into a container of the
    Traefik image (pulled by ``build``) and looks for the state file.
    """
    state_dir = settings["workdir"] / STATE_DIR
    image = f"traefik:{_context(settings)['ifdd_proxy_traefik_version']}"
    argv = [
        "docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
        "--volume", f"{state_dir}:/check:ro", image,
        "-c", f"test -f /check/{STATE_FILE}",
    ]  # fmt: skip
    run.overview("Checking that Docker sees the work directory")
    result = subprocess.run(
        argv, cwd=settings["workdir"], capture_output=True, text=True, check=False
    )
    run.note(f"$ {' '.join(argv)} -> exit code {result.returncode}")
    if result.returncode == NOT_SEEN:
        raise CreateError(
            f"Docker does not see {settings['workdir']}: its bind mounts would be empty "
            "directories. With Colima add the directory (writable) to 'mounts:' in "
            "~/.colima/<profile>/colima.yaml and restart Colima "
            "(docs/local-macos-colima.md)"
        )
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
        raise CreateError(f"checking the mounts with {image} failed: {output[0]}")


def _context(settings: Settings) -> dict:
    return yaml.safe_load(settings["variables"].read_text(encoding="utf-8"))["default_context"]


def _networks(settings: Settings, instances: dict[str, Path]) -> list[tuple[str, bool]]:
    """(name, internal) of the external networks the instances share."""
    context = _context(settings)
    networks = [(context["ifdd_proxy_network"], False)]
    if "ts" in instances or "ts-api" in instances:
        networks.append((context["ifdd_ts_ingest_network"], False))
    if "lab" in instances:
        # No route outside; the proxy joins it (ifdd_proxy_extra_networks).
        networks.append((context["ifdd_lab_network"], True))
    return networks


def _remove_volumes_except(
    settings: Settings, service: str, path: Path, keep: tuple[str, ...], run: Run
) -> None:
    """Removes the Compose volumes of an instance except ``keep`` (``down --purge``).

    A new instance of the same name (same Compose project) finds the kept
    volumes again; ``docker volume rm`` removes them for good.
    """
    config = subprocess.run(
        [*compose(settings), "config", "--format", "json"],
        cwd=path, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if config.returncode != 0:
        raise CreateError(f"{service}: docker compose config failed: {config.stderr.strip()}")
    project = json.loads(config.stdout)["name"]
    listed = subprocess.run(
        [
            "docker", "volume", "ls", "--quiet", "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    for volume in listed.stdout.split():
        if volume.removeprefix(f"{project}_") in keep:
            run.overview(
                f"Kept volume {volume} ({service}); remove it with: docker volume rm {volume}"
            )
        else:
            run.command(["docker", "volume", "rm", volume], cwd=settings["workdir"])


def refuse_if_protected(settings: Settings, what: str) -> None:
    """A protected installation (a stage) is only updated, never deleted and created again."""
    if settings["protected"]:
        raise CreateError(
            f"installation {settings['installation']} is protected (protected: true in its "
            f"description): no {what}; use update. To tear it down after all, remove "
            "protected from the description and compile again"
        )


def _keep_persistent(settings: Settings, instances: dict[str, Path], run: Run) -> None:
    for service, path in instances.items():
        for relative in PERSIST.get(service, ()):
            source = path / relative
            if source.is_dir():
                target = settings["persist"] / service / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, dirs_exist_ok=True)
                target.chmod(0o700)  # database dumps hold password hashes
                run.overview(f"Kept {service}/{relative}/ in {settings['persist']}")
            elif source.is_file():
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
