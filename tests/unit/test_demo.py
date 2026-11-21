"""demo load with a fake Keycloak admin and a fake ct instance."""

import json
import stat

import pytest

from ifdd_meta import demo
from ifdd_meta.create import STATE_DIR, STATE_FILE
from ifdd_meta.runner import Run, StepError
from ifdd_meta.settings import load

VARIABLES = {
    "ifdd_keycloak_group_nodes_readers": "ifdd_nodes_readers",
    "ifdd_keycloak_group_nodes_writers": "ifdd_nodes_writers",
    "ifdd_keycloak_group_nodes_privileged": "ifdd_nodes_privileged",
    "ifdd_keycloak_wf_admin_group": "ifdd_wf_admins",
    "ifdd_context_root": "ifdd.de",
    "ifdd_ts_role_viewer": "readonly",
    "ifdd_keycloak_lab_users_group": "ifdd_lab_users",
}


class FakeAdmin:
    def __init__(self):
        self.users = {}

    def ensure_user(
        self, username, password, *, groups=(), realm_roles=(), first_name="", last_name=""
    ):
        created = username not in self.users
        self.users[username] = {
            "password": password,
            "groups": list(groups),
            "realm_roles": list(realm_roles),
        }
        return f"id-{username}", created


def installation(tmp_path, instances, environ=None, ct_exit=0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "vars.yaml").write_text(json.dumps({"default_context": VARIABLES}))
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")
    work = tmp_path / "work"
    (work / STATE_DIR).mkdir(parents=True)
    (work / STATE_DIR / STATE_FILE).write_text(json.dumps(instances))
    if "ct" in instances:
        ct = work / instances["ct"]
        ct.mkdir()
        script = ct / "adm_02_run.sh"
        script.write_text(f'#!/bin/sh\necho "$@" >> calls.txt\nexit {ct_exit}\n')
        script.chmod(0o755)
    return load(cwd=tmp_path, environ=environ or {})


def run_load(settings, admin):
    lines = []
    run = Run(settings["workdir"] / "logs", "demo-load", lines.append)
    demo.load(settings, run, admin_factory=lambda _: admin)
    return lines


ALL = {
    "keycloak": "keycloak",
    "ct": "ct",
    "ts": "time-series",
    "ts-api": "time-series-api",
    "wf": "wf",
}


def test_new_password_goes_into_the_env_file_and_is_not_shown(tmp_path):
    settings = installation(tmp_path, ALL)
    admin = FakeAdmin()
    lines = run_load(settings, admin)
    env = tmp_path / ".env"
    password = env.read_text().strip().split("=", 1)[1]
    assert admin.users["ifdd-demo"]["password"] == password and len(password) >= 20
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert not any(password in line for line in lines)
    assert not any(password in line for line in settings["workdir"].joinpath("logs").glob("*")
                   for line in [line.read_text()])  # fmt: skip
    assert admin.users["ifdd-demo"]["groups"] == [
        "ifdd_nodes_readers", "ifdd_nodes_writers", "ifdd_nodes_privileged",
        "ifdd_wf_admins", "ifdd_ctx_ifdd.de",
    ]  # fmt: skip
    assert admin.users["ifdd-demo"]["realm_roles"] == ["readonly"]  # Grafana viewer
    calls = (settings["workdir"] / "ct" / "calls.txt").read_text().splitlines()
    assert calls == [f"-f {demo.GRID}", f"-f {demo.SERIES}"]
    assert "==> Demo user ifdd-demo created" in lines


def test_a_known_password_is_used_and_a_second_run_updates(tmp_path):
    environ = {"IFDD_META_DEMO_PASSWORD": "known-one"}
    settings = installation(tmp_path, ALL, environ)
    admin = FakeAdmin()
    run_load(settings, admin)
    lines = run_load(settings, admin)
    assert admin.users["ifdd-demo"]["password"] == "known-one"
    assert not (tmp_path / ".env").exists()
    assert "==> Demo user ifdd-demo updated (password, groups, roles)" in lines


def test_without_wf_ts_api_and_ct(tmp_path):
    settings = installation(tmp_path, {"keycloak": "keycloak", "ct": "ct"})
    admin = FakeAdmin()
    run_load(settings, admin)
    assert "ifdd_wf_admins" not in admin.users["ifdd-demo"]["groups"]
    assert admin.users["ifdd-demo"]["realm_roles"] == []  # no Grafana without ts
    calls = (settings["workdir"] / "ct" / "calls.txt").read_text().splitlines()
    assert calls == [f"-f {demo.GRID}"]  # no time series without ts-api
    settings = installation(tmp_path / "x", {"keycloak": "keycloak"})
    assert "==> No ct instance: no demo data" in run_load(settings, FakeAdmin())


def test_errors(tmp_path):
    settings = installation(tmp_path, {"be": "backend"})
    with pytest.raises(demo.DemoError, match="no keycloak instance"):
        run_load(settings, FakeAdmin())
    settings = installation(tmp_path / "ct", ALL, ct_exit=3)
    with pytest.raises(StepError, match="exit code 3"):
        run_load(settings, FakeAdmin())


def test_lab_users_group_only_with_lab(tmp_path):
    admin = FakeAdmin()
    run_load(installation(tmp_path / "a", {"keycloak": "keycloak", "lab": "lab"}), admin)
    groups = admin.users["ifdd-demo"]["groups"]
    assert "ifdd_lab_users" in groups
    assert not any("admin" in g for g in groups if "lab" in g)  # users, not admins
    admin = FakeAdmin()
    run_load(installation(tmp_path / "b", {"keycloak": "keycloak"}), admin)
    assert "ifdd_lab_users" not in admin.users["ifdd-demo"]["groups"]
