"""build, up, down, status and logs against a fake docker and fake adm scripts."""

import json
import os

import pytest

from ifdd_meta import create, services
from ifdd_meta.runner import Run
from ifdd_meta.settings import SERVICES, load

DIRECTORY = {"be": "backend", "ui": "frontend", "ts": "time-series", "ts-api": "time-series-api"}

FAKE_DOCKER = """#!/bin/sh
echo "$(basename "$PWD") docker $*" >> "$CALLS"
case "$*" in
  "network inspect"*) [ -f "$NETWORKS/$3" ] ;;
  "network create"*) eval "last=\\${$#}"; touch "$NETWORKS/$last" ;;
  "network rm"*) rm -f "$NETWORKS/$3" ;;
  *" ps --all --format json")
    echo '{"Name":"'"$(basename "$PWD")"'-1","State":"running","Health":"healthy"}' ;;
  *" logs "*) echo "log of $(basename "$PWD")" ;;
  *" config --format json") echo '{"name": "'"$(basename "$PWD")"'"}' ;;
  "volume ls "*)  # the volumes of the project named in the filter
    project=${5#label=com.docker.compose.project=}
    printf '%s\n' "${project}_dind-data" "${project}_postgres-data" "${project}_dind-socket" ;;
  "run "*)  # the mount check: exit code from $RUN_EXIT (1: file not seen)
    [ -n "$RUN_EXIT" ] && { echo "docker says no" >&2; exit "$RUN_EXIT"; }
    case "$*" in *"/.ifdd-meta:/check:ro traefik:v9 -c test -f /check/instances.json") ;;
      *) exit 99 ;; esac ;;
esac
"""

FAKE_SCRIPT = """#!/bin/sh
echo "$(basename "$PWD") $(basename "$0") $*" >> "$CALLS"
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    (tmp_path / "networks").mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CALLS", str(tmp_path / "calls"))
    monkeypatch.setenv("NETWORKS", str(tmp_path / "networks"))

    work = tmp_path / "work"
    state = {}
    for service in SERVICES:
        directory = DIRECTORY.get(service, service)
        state[service] = directory
        (work / directory).mkdir(parents=True)
        for script in ("adm_01_build.sh", "adm_01_plugins.sh", "adm_02_run.sh"):
            path = work / directory / script
            path.write_text(FAKE_SCRIPT)
            path.chmod(0o755)
    (work / ".ifdd-meta").mkdir()
    (work / ".ifdd-meta" / "instances.json").write_text(json.dumps(state))
    (tmp_path / "vars.yaml").write_text(
        "default_context:\n  ifdd_proxy_network: net-a\n  ifdd_ts_ingest_network: net-b\n"
        "  ifdd_lab_network: net-c\n  ifdd_proxy_traefik_version: v9\n"
    )
    # every service, lab included (it is optional)
    (tmp_path / "ifdd-meta.yaml").write_text(
        f"variables: vars.yaml\nwait_timeout: 7\nservices: [{', '.join(SERVICES)}]\n"
    )
    return tmp_path


def settings(world):
    return load(cwd=world, environ={})


def calls(world) -> list[str]:
    path = world / "calls"
    return path.read_text().splitlines() if path.exists() else []


def run_for(world, name="test"):
    lines: list[str] = []
    return Run(world / "work" / "logs", name, lines.append), lines


def test_build_uses_compose_and_the_adm_scripts(world):
    run, lines = run_for(world)
    services.build(settings(world), run)
    compose = "docker compose -f docker-compose-production.yml"
    assert calls(world) == [
        f"proxy {compose} pull",
        f"keycloak {compose} pull",
        f"backend {compose} build",
        f"frontend {compose} build",
        f"schema {compose} pull",
        f"type {compose} pull",
        f"unit {compose} pull",
        "ct adm_01_build.sh ",
        f"time-series {compose} pull",
        "time-series adm_01_plugins.sh ",
        f"time-series-api {compose} build",
        "wf adm_01_build.sh production",
        "lab adm_01_build.sh ",
    ]
    assert lines[0] == "==> Building proxy (production)"


def test_up_creates_networks_and_keeps_the_order(world):
    run, _ = run_for(world)
    services.up(settings(world), run)
    compose = "docker compose -f docker-compose-production.yml"
    wait = "--wait --wait-timeout 7"
    started = [c for c in calls(world) if " up " in c or "adm_02" in c]
    assert [c for c in calls(world) if "network create" in c] == [
        "work docker network create net-a",
        "work docker network create net-b",
        "work docker network create --internal net-c",
    ]
    assert started == [
        f"proxy {compose} up --detach {wait} proxy",
        f"keycloak {compose} up --detach {wait}",
        f"backend {compose} up --detach {wait}",
        f"frontend {compose} up --detach {wait}",
        f"schema {compose} up --detach {wait}",
        f"type {compose} up --detach {wait}",
        f"unit {compose} up --detach {wait}",
        f"proxy {compose} up --detach {wait}",
        "wf adm_02_run.sh production",
        f"time-series adm_02_run.sh production {wait}",
        f"time-series-api {compose} up --detach {wait}",
        "lab adm_02_run.sh ",
    ]
    # networks that exist are not created again
    services.up(settings(world), run)
    assert len([c for c in calls(world) if "network create" in c]) == 3


def test_up_checks_first_that_docker_sees_the_work_directory(world, monkeypatch):
    run, lines = run_for(world)
    services.up(settings(world), run)
    first = calls(world)[0]
    assert first.startswith("work docker run --rm --network none --entrypoint /bin/sh")
    assert lines[0] == "==> Checking that Docker sees the work directory"


@pytest.mark.parametrize(
    ("code", "message"),
    [("1", "Docker does not see .*/work: .*colima.yaml"), ("125", "failed: docker says no")],
)
def test_up_stops_before_anything_starts_when_the_check_fails(world, monkeypatch, code, message):
    monkeypatch.setenv("RUN_EXIT", code)
    run, _ = run_for(world)
    with pytest.raises(create.CreateError, match=message):
        services.up(settings(world), run)
    assert [c for c in calls(world) if " run " not in c] == []  # no network, no service


def test_up_only_some_services(world):
    run, _ = run_for(world)
    services.up(settings(world), run, only=["be", "proxy"])
    started = [c.split()[0] for c in calls(world) if " up " in c]
    assert started == ["proxy", "backend", "proxy"]
    assert [c for c in calls(world) if "net-b" in c] == []


def test_down_and_purge_keep_the_acme_state(world):
    s = settings(world)
    acme = world / "work" / "proxy" / "letsencrypt" / "acme.json"
    acme.parent.mkdir()
    acme.write_text("{}")
    run, _ = run_for(world)
    services.up(s, run)
    services.down(s, run, purge=True)
    stopped = [c.split()[0] for c in calls(world) if " down " in c]
    assert stopped == [
        "lab", "time-series-api", "time-series", "wf", "proxy", "unit", "type",
        "schema", "frontend", "backend", "keycloak",
    ]  # fmt: skip
    downs = [c for c in calls(world) if " down " in c]
    assert all("--volumes" in c for c in downs if not c.startswith("lab "))
    # lab keeps the Docker-in-Docker data (the homes): no --volumes, the others removed
    assert "lab docker compose -f docker-compose-production.yml down --remove-orphans" in downs
    removed = [c for c in calls(world) if "volume rm" in c]
    assert removed == [
        "work docker volume rm lab_postgres-data",
        "work docker volume rm lab_dind-socket",
    ]
    assert sorted(p.name for p in (world / "work").iterdir()) == ["logs"]
    kept = world / "state" / "proxy" / "letsencrypt" / "acme.json"
    assert kept.read_text() == "{}"
    assert oct(kept.stat().st_mode & 0o777) == "0o600"
    assert list((world / "networks").iterdir()) == []


def test_purge_with_only_is_refused(world):
    run, _ = run_for(world)
    with pytest.raises(create.CreateError, match="do not combine"):
        services.down(settings(world), run, only=["be"], purge=True)
    assert calls(world) == []


def test_status_and_logs(world):
    rows = services.status(settings(world))
    assert ("be", "backend-1", "running", "healthy") in rows
    assert all(row[0] not in ("ct", "model") for row in rows)
    assert {row[0] for row in services.status(settings(world), only=["be", "ui"])} == {"be", "ui"}
    run, _ = run_for(world)
    written = services.logs(settings(world), run, world / "out")
    assert (world / "out" / "be.log").read_text() == "log of backend\n"
    assert len(written) == len(SERVICES) - 2


def test_without_instances(tmp_path):
    (tmp_path / "vars.yaml").write_text("default_context: {}\n")
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")
    run = Run(tmp_path / "logs", "test", lambda line: None)
    with pytest.raises(create.CreateError, match="run 'create' first"):
        services.up(load(cwd=tmp_path, environ={}), run)


def test_parse_ps_accepts_array_and_lines():
    assert services._parse_ps('[{"Name": "a"}]') == [{"Name": "a"}]
    assert services._parse_ps('{"Name": "a"}\n{"Name": "b"}\n') == [{"Name": "a"}, {"Name": "b"}]
    assert services._parse_ps("") == []


def test_restored_acme_state_after_create(tmp_path):
    kept = tmp_path / "state" / "proxy" / "letsencrypt" / "acme.json"
    kept.parent.mkdir(parents=True)
    kept.write_text('{"acme": 1}')
    (tmp_path / "ifdd-meta.yaml").write_text("services: [proxy]\n")
    s = load(cwd=tmp_path, environ={})
    instance = tmp_path / "work" / "proxy"
    instance.mkdir(parents=True)
    run = Run(tmp_path / "logs", "test", lambda line: None)
    create._restore_persistent(s, "proxy", instance, run)
    assert (instance / "letsencrypt" / "acme.json").read_text() == '{"acme": 1}'


def test_wf_git_token_goes_into_kestra_env(tmp_path):
    (tmp_path / "ifdd-meta.yaml").write_text("services: [wf]\n")
    (tmp_path / ".env").write_text("IFDD_META_WF_GIT_TOKEN=tok\n")
    s = load(cwd=tmp_path, environ={})
    secrets = tmp_path / "work" / "wf" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "kestra.env").write_text("SECRET_GIT_TOKEN=\n")
    run = Run(tmp_path / "logs", "test", lambda line: None)
    create._wf_git_token(s, {"wf": "wf"}, run)
    assert (secrets / "kestra.env").read_text() == "SECRET_GIT_TOKEN=dG9r\n"


def test_secrets_are_not_shown_in_settings(tmp_path):
    (tmp_path / ".env").write_text("IFDD_META_WF_GIT_TOKEN=tok\nOTHER=x\n")
    s = load(cwd=tmp_path, environ={"IFDD_META_DEMO_PASSWORD": "pw"})
    assert s.secrets == {"IFDD_META_WF_GIT_TOKEN": "tok", "IFDD_META_DEMO_PASSWORD": "pw"}
    assert "tok" not in repr(s)


def test_up_starts_the_git_server_before_wf(world, monkeypatch):
    (world / "ifdd-meta.yaml").write_text("variables: vars.yaml\nwait_timeout: 7\nwf_git: local\n")
    state_file = world / "work" / ".ifdd-meta" / "instances.json"
    state = json.loads(state_file.read_text()) | {"git": "git"}
    state_file.write_text(json.dumps(state))
    (world / "work" / "git").mkdir()
    provisioned = []
    monkeypatch.setattr(
        services.gitserver,
        "provision",
        lambda path, compose, context, kestra_env, run: provisioned.append((path.name, kestra_env)),
    )
    run, _ = run_for(world)
    services.up(settings(world), run)
    started = [c.split()[0] for c in calls(world) if " up " in c or "adm_02" in c]
    assert started.index("git") == started.index("wf") - 1
    assert provisioned == [("git", world / "work" / "wf" / "secrets" / "kestra.env")]
    # without wf there is no Git server
    assert "git" not in services._instances(settings(world), ["be"])
    services.down(settings(world), run)
    stopped = [c.split()[0] for c in calls(world) if " down " in c]
    assert stopped.index("git") == stopped.index("wf") + 1
    assert "git docker compose down --remove-orphans" in calls(world)
