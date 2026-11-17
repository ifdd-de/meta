import stat
import sys

import pytest

from ifdd_meta.runner import Run, StepError


def test_log_is_private_and_holds_the_output(tmp_path):
    lines = []
    run = Run(tmp_path / "logs", "test", lines.append)
    run.overview("Step one")
    run.command([sys.executable, "-c", "print('hello from the command')"], cwd=tmp_path)
    assert stat.S_IMODE(run.log.stat().st_mode) == 0o600
    assert lines == ["==> Step one"]
    text = run.log.read_text()
    assert "==> Step one" in text and "hello from the command" in text


def test_failure_shows_the_tail_of_the_log(tmp_path):
    run = Run(tmp_path, "test", lambda line: None)
    with pytest.raises(StepError, match="exit code 3") as error:
        run.command([sys.executable, "-c", "print('the reason'); raise SystemExit(3)"], tmp_path)
    assert "the reason" in str(error.value)


def test_verbose_shows_only_the_new_output(tmp_path):
    lines = []
    run = Run(tmp_path, "test", lines.append, verbose=True)
    run.command([sys.executable, "-c", "print('first')"], cwd=tmp_path)
    run.command([sys.executable, "-c", "print('second')"], cwd=tmp_path)
    assert lines == ["first", "second"]
