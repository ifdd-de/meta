"""rebuild: order of the steps, status file, first failure stops the run."""

import json

import pytest
from typer.testing import CliRunner

from ifdd_meta import rebuild, services
from ifdd_meta.cli import app
from ifdd_meta.create import STATE_DIR, STATE_FILE
from ifdd_meta.runner import Run, StepError
from ifdd_meta.settings import load


def settings_in(tmp_path, instances=None):
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")
    if instances is not None:
        state = tmp_path / "work" / STATE_DIR
        state.mkdir(parents=True)
        (state / STATE_FILE).write_text(json.dumps(instances))
    return load(cwd=tmp_path, environ={})


def recording_steps(calls, fail=None):
    def step(name):
        def run_step(settings, run):
            calls.append(name)
            if name == fail:
                raise StepError(f"{name} failed\nlast lines …")

        return run_step

    return {name: step(name) for name in rebuild.DEFAULT_STEPS}


def status_lines(settings):
    return rebuild.status_path(settings).read_text(encoding="utf-8").splitlines()


def test_plan_without_options():
    assert rebuild.plan() == ["down", "create", "build", "up"]


def test_plan_of_the_demo_server():
    assert rebuild.plan(prune=True, demo_data=True, test=True) == [
        "down", "create", "build", "prune-build-cache", "up", "prune-images", "demo", "test",
    ]  # fmt: skip


def test_steps_run_in_order_and_the_status_says_ok(tmp_path):
    settings = settings_in(tmp_path)
    run = Run(settings["workdir"] / "logs", "rebuild", lambda _: None)
    calls = []
    names = rebuild.plan(prune=True, demo_data=True, test=True)
    rebuild.rebuild(
        settings, run, names, tests=lambda: calls.append("test") or 0,
        steps=recording_steps(calls),
    )  # fmt: skip
    assert calls == names
    lines = status_lines(settings)
    assert lines[-1] == "result: ok"
    assert f"steps: {' '.join(names)}" in lines
    assert f"log: {run.log}" in lines
    assert rebuild.status_path(settings).parent == tmp_path / "state"


def test_first_failure_stops_and_is_recorded(tmp_path):
    settings = settings_in(tmp_path)
    run = Run(settings["workdir"] / "logs", "rebuild", lambda _: None)
    calls = []
    with pytest.raises(StepError):
        rebuild.rebuild(
            settings, run, rebuild.plan(demo_data=True), steps=recording_steps(calls, "build")
        )
    assert calls == ["down", "create", "build"]
    lines = status_lines(settings)
    assert "result: failed" in lines
    assert "step: build" in lines
    assert "error: build failed" in lines  # only the first line of the error


def test_failed_tests_fail_the_run(tmp_path):
    settings = settings_in(tmp_path)
    run = Run(settings["workdir"] / "logs", "rebuild", lambda _: None)
    with pytest.raises(rebuild.RebuildError, match="exit code 1"):
        rebuild.rebuild(
            settings, run, rebuild.plan(test=True), tests=lambda: 1,
            steps=recording_steps([]),
        )  # fmt: skip
    assert "step: test" in status_lines(settings)


def test_down_is_skipped_without_instances(tmp_path, monkeypatch):
    settings = settings_in(tmp_path)
    lines = []
    run = Run(settings["workdir"] / "logs", "rebuild", lines.append)
    monkeypatch.setattr(services, "down", lambda *a, **k: pytest.fail("down must not run"))
    rebuild.DEFAULT_STEPS["down"](settings, run)
    assert any("nothing to tear down" in line for line in lines)


def test_down_purges_existing_instances(tmp_path, monkeypatch):
    settings = settings_in(tmp_path, {"proxy": "proxy"})
    run = Run(settings["workdir"] / "logs", "rebuild", lambda _: None)
    seen = {}
    monkeypatch.setattr(services, "down", lambda s, r, purge=False: seen.update(purge=purge))
    rebuild.DEFAULT_STEPS["down"](settings, run)
    assert seen == {"purge": True}


def test_cli_reports_the_status_file_on_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")

    def failing(settings, run, names, tests=None):
        assert names == ["down", "create", "build", "up", "demo"]
        raise StepError("create failed")

    monkeypatch.setattr(rebuild, "rebuild", failing)
    result = CliRunner().invoke(app, ["rebuild", "--demo"])
    assert result.exit_code == 1
    assert "error: create failed" in result.output
    assert f"status: {tmp_path / 'state' / rebuild.STATUS_FILE}" in result.output
