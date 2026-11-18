import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ifdd_meta import testrun
from ifdd_meta.settings import load

DEMO = Path(__file__).parents[2] / "config" / "demo"


def test_arguments_isolate_pytest_and_keep_assertions_plain(tmp_path):
    args = testrun.arguments(junit=tmp_path / "j.xml", keyword="be", exitfirst=True)
    assert args[0] == str(testrun.directory())
    assert (testrun.directory() / "conftest.py").is_file()
    for option in ("--assert=plain", "no:cacheprovider", "-x"):
        assert option in args
    assert args[args.index("-c") + 1] == "/dev/null"
    assert args[args.index("--junitxml") + 1] == str(tmp_path / "j.xml")
    assert args[args.index("-k") + 1] == "be"


def test_environment_names_the_file_and_passes_command_line_values(tmp_path):
    (tmp_path / "ifdd-meta.yaml").write_text("workdir: w\n")
    cli = {"workdir": Path("other"), "templates.ref": None}
    settings = load(settings_file=tmp_path / "ifdd-meta.yaml", cli=cli, cwd=tmp_path, environ={})
    variables = testrun.environment(settings, cli)
    assert variables == {
        "IFDD_META_TEST_SETTINGS": str(tmp_path / "ifdd-meta.yaml"),
        "IFDD_META_WORKDIR": str(tmp_path / "other"),
    }


def test_environment_needs_a_settings_file(tmp_path):
    settings = load(cwd=tmp_path, environ={})
    with pytest.raises(ValueError, match="need a settings file"):
        testrun.environment(settings, {})


def ifdd_meta(*args, cwd):
    command = [sys.executable, "-c", "from ifdd_meta.cli import main; main()", *args]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("mode", ["read", "write"])
def test_without_instances_every_test_is_skipped(tmp_path, mode):
    """Collects all integration tests (imports, fixtures) without an installation."""
    shutil.copy(DEMO / "cookiecutter_all.yaml", tmp_path)
    (tmp_path / "ifdd-meta.yaml").write_text(f"tests: {mode}\n")
    result = ifdd_meta("--settings", "ifdd-meta.yaml", "test", "--junit", "j.xml", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = result.stdout.strip().splitlines()[-1]
    assert "skipped" in summary and "passed" not in summary and "error" not in summary
    assert (tmp_path / "j.xml").is_file()
    assert not (tmp_path / ".pytest_cache").exists()
