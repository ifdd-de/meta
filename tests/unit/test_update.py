"""update against a small fake template with two releases, with the real cruft."""

import json
import subprocess
from pathlib import Path

import pytest

from ifdd_meta import create, update
from ifdd_meta.runner import Run
from ifdd_meta.settings import load

GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.org"]

V1 = {
    "cookiecutter.json": {
        "ifdd_be_prj_name": "backend",
        "ifdd_be_prj_slug": "{{ cookiecutter.ifdd_be_prj_name }}",
        "ifdd_be_version": "1",
        "ifdd_be_level": "info",
        "ifdd_shared": "x",
    },
    "README.md": "be {{ cookiecutter.ifdd_be_version }} {{ cookiecutter.ifdd_shared }}\n"
    "line 2\nline 3\nline 4\nline 5\n",
    "dotenv.dist": "IFDD_BE_VERSION={{ cookiecutter.ifdd_be_version }}\nIFDD_BE_SECRET=\n"
    "IFDD_BE_LEVEL=info\nIFDD_BE_OLD=1\n",
    ".env": "IFDD_BE_VERSION={{ cookiecutter.ifdd_be_version }}\nIFDD_BE_SECRET=s3cret\n"
    "IFDD_BE_LEVEL=info\nIFDD_BE_OLD=1\n",
    ".gitignore": ".env\n",
    # cruft 2.16.0 appends this list to the skip list of .cruft.json on every update
    "pyproject.toml": '[tool.cruft]\nskip = [".env", "notes.txt"]\n',
}


def v2(files: dict) -> dict:
    files = json.loads(json.dumps(files))
    files["cookiecutter.json"]["ifdd_be_version"] = "2"  # owned by the template
    files["README.md"] = files["README.md"].replace("line 2", "line 2 of release 2")
    files["dotenv.dist"] = (
        "IFDD_BE_VERSION={{ cookiecutter.ifdd_be_version }}\nIFDD_BE_SECRET=\n"
        "IFDD_BE_LEVEL=warn\nIFDD_BE_NEW=1\n"
    )
    return files


def write_release(repo: Path, files: dict, tag: str) -> None:
    body = repo / "{{cookiecutter.ifdd_be_prj_slug}}"
    body.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        if name == "cookiecutter.json":
            (repo / name).write_text(json.dumps(content))
        else:
            (body / name).write_text(content)
    subprocess.run([*GIT, "-C", str(repo), "add", "--force", "."], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", tag], check=True)
    subprocess.run([*GIT, "-C", str(repo), "tag", tag], check=True)


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    repo = root / "be"
    repo.mkdir(parents=True)
    subprocess.run([*GIT, "init", "-q", "-b", "main", str(repo)], check=True)
    write_release(repo, V1, "v1")
    write_release(repo, v2(V1), "v2")
    return root


@pytest.fixture(autouse=True)
def git_identity(tmp_path_factory, monkeypatch):
    config = tmp_path_factory.mktemp("git") / "config"
    config.write_text("[user]\n\tname = Test\n\temail = test@example.org\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def installation(directory: Path, templates: Path, ref: str, shared: str = "x"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "vars.yaml").write_text(f"default_context:\n  ifdd_shared: {shared}\n")
    (directory / "ifdd-meta.yaml").write_text(
        f"variables: vars.yaml\nservices: [be]\ntemplates:\n  source: {templates}\n  ref: {ref}\n"
    )
    return load(cwd=directory, environ={})


def run_for(settings, name):
    lines = []
    return Run(settings["workdir"] / "logs", name, lines.append), lines


def git(path: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=path, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def created(tmp_path, templates):
    """An instance created at v1, with a value changed by hand in its .env."""
    settings = installation(tmp_path / "old", templates, "v1")
    create.create(settings, run_for(settings, "create")[0], is_root=lambda: False)
    path = settings["workdir"] / "backend"
    env = path / ".env"
    env.write_text(env.read_text().replace("IFDD_BE_LEVEL=info", "IFDD_BE_LEVEL=debug"))
    env.chmod(0o600)
    return path


def test_update_gives_what_a_fresh_create_gives(tmp_path, templates, created):
    settings = installation(tmp_path / "old", templates, "v2", shared="y")
    run, lines = run_for(settings, "update")
    assert update.update(settings, run) == ["be"]

    fresh_settings = installation(tmp_path / "fresh", templates, "v2", shared="y")
    create.create(fresh_settings, run_for(fresh_settings, "create")[0], is_root=lambda: False)
    fresh = fresh_settings["workdir"] / "backend"

    tracked = git(created, "ls-files").split()
    assert tracked == git(fresh, "ls-files").split()
    for name in tracked:
        assert (created / name).read_text() == (fresh / name).read_text(), name
    # the template-owned default and the shared value from the description
    assert (created / "README.md").read_text().startswith("be 2 y\n")
    # skip list as create wrote it, although cruft appends pyproject's list
    assert (
        json.loads((created / ".cruft.json").read_text())["skip"]
        == json.loads((fresh / ".cruft.json").read_text())["skip"]
    )
    assert git(created, "log", "--format=%s").splitlines() == [
        "Update be to v2",
        "Create be from its template at v1",
    ]
    assert git(created, "status", "--porcelain") == ""
    # .env: the old default follows, the hand-made value and the secret stay
    assert (created / ".env").read_text() == (
        "IFDD_BE_VERSION=2\nIFDD_BE_SECRET=s3cret\nIFDD_BE_LEVEL=debug\nIFDD_BE_OLD=1\n"
    )
    assert (created / ".env").stat().st_mode & 0o777 == 0o600
    assert "==> be: new defaults of ifdd_be_version" in lines
    assert "==> be: .env: new defaults of IFDD_BE_VERSION" in lines
    assert "==> be: .env: new settings in dotenv.dist: IFDD_BE_NEW; add them by hand" in lines
    assert "==> be: .env: no longer in dotenv.dist: IFDD_BE_OLD" in lines
    assert not any("s3cret" in line for line in lines)
    # a second run has nothing to do
    run, lines = run_for(settings, "update")
    assert update.update(settings, run) == []
    assert lines == ["==> be: up to date (v2)"]


def test_check_changes_nothing(tmp_path, templates, created):
    head = git(created, "rev-parse", "HEAD")
    settings = installation(tmp_path / "old", templates, "v2")
    run, lines = run_for(settings, "check")
    (plan,) = update.check(settings, run)
    assert plan.needed and plan.changed_defaults == ["ifdd_be_version"]
    assert lines[0].startswith("==> be: would update ")
    assert lines[0].endswith("(v2); new defaults: ifdd_be_version")
    assert git(created, "rev-parse", "HEAD") == head
    assert git(created, "status", "--porcelain") == ""


def test_uncommitted_changes_stop_before_anything(tmp_path, templates, created):
    (created / "README.md").write_text("changed by hand\n")
    settings = installation(tmp_path / "old", templates, "v2")
    with pytest.raises(update.UpdateError, match="be: uncommitted changes"):
        update.update(settings, run_for(settings, "update")[0])
    assert len(git(created, "log", "--format=%s").splitlines()) == 1


def test_an_instance_without_git_is_refused(tmp_path, templates, created):
    subprocess.run(["rm", "-rf", str(created / ".git")], check=True)
    settings = installation(tmp_path / "old", templates, "v2")
    with pytest.raises(update.UpdateError, match="is no Git repository"):
        update.update(settings, run_for(settings, "update")[0])


def test_a_conflict_stops_and_commits_nothing(tmp_path, templates, created):
    readme = created / "README.md"
    readme.write_text(readme.read_text().replace("line 2", "line 2 changed by hand"))
    git(created, "commit", "-q", "-am", "Local change")
    settings = installation(tmp_path / "old", templates, "v2")
    with pytest.raises(update.UpdateError, match="be: conflicts in README.md"):
        update.update(settings, run_for(settings, "update")[0])
    assert git(created, "log", "--format=%s").splitlines()[0] == "Local change"


def test_a_local_change_elsewhere_is_merged(tmp_path, templates, created):
    readme = created / "README.md"
    readme.write_text(readme.read_text().replace("line 5", "line 5 changed by hand"))
    git(created, "commit", "-q", "-am", "Local change")
    settings = installation(tmp_path / "old", templates, "v2")
    assert update.update(settings, run_for(settings, "update")[0]) == ["be"]
    assert readme.read_text() == (
        "be 2 x\nline 2 of release 2\nline 3\nline 4\nline 5 changed by hand\n"
    )


def test_variables_to_pass():
    old = {"a": "1", "b": "{{ cookiecutter.a }}", "own": "1", "same": "s", "_p": 1}
    new = {"a": "1", "b": "{{ cookiecutter.a }}", "own": "2", "same": "s", "added": "n", "_p": 2}
    values, changed = update.variables_to_pass({"a": "A", "other": "o"}, old, new)
    assert values == {"a": "A", "own": "2"}  # only names the template knows; no private ones
    assert changed == ["own"]


def test_merge_env_keeps_hand_made_values_quotes_and_export():
    current = 'export V="1"\nS=secret\nL=debug\n# comment\n'
    old_dist = 'V="1"\nS=\nL=info\n'
    new_dist = 'V="2"\nS=\nL=warn\nN=1\n'
    text, changed, added, removed = update.merge_env(current, old_dist, new_dist)
    assert text == 'export V="2"\nS=secret\nL=debug\n# comment\n'
    assert (changed, added, removed) == (["V"], ["N"], [])


@pytest.mark.parametrize(
    ("dist", "target"),
    [("dotenv.dist", ".env"), ("secrets/kestra.env.dist", "secrets/kestra.env")],
)
def test_target_of(dist, target):
    assert update.target_of(dist) == target
