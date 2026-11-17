"""Run external commands with one overview line on the console and a full log."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

TAIL_LINES = 20


class StepError(RuntimeError):
    """A step failed; the message ends with the last lines of the log."""


class Run:
    """One command of ifdd-meta: an overview on the console, everything in the log."""

    def __init__(self, logdir: Path, command: str, echo: Callable[[str], None], verbose=False):
        logdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.log = logdir / f"{command}-{stamp}.log"
        self.log.touch(mode=0o600)
        os.chmod(self.log, 0o600)
        self._echo = echo
        self._verbose = verbose

    def overview(self, message: str) -> None:
        self._echo(f"==> {message}")
        self._write(f"==> {message}\n")

    def note(self, message: str) -> None:
        """A line for the log only (shown with --verbose)."""
        if self._verbose:
            self._echo(message)
        self._write(message + "\n")

    def command(self, argv: Sequence[str], cwd: Path, env: dict[str, str] | None = None) -> None:
        """Run a command; its output goes into the log. Fails with the log's tail."""
        self._write(f"$ (cd {cwd} && {' '.join(map(str, argv))})\n")
        start = self.log.stat().st_size
        with self.log.open("a", encoding="utf-8") as log:
            result = subprocess.run(
                [str(a) for a in argv],
                cwd=cwd,
                env={**os.environ, **(env or {})},
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        if self._verbose:
            with self.log.open("rb") as log:
                log.seek(start)
                output = log.read().decode("utf-8", errors="replace").rstrip()
            if output:
                self._echo(output)
        if result.returncode != 0:
            raise StepError(
                f"{argv[0]} … failed with exit code {result.returncode}; "
                f"last lines of {self.log}:\n{self._tail(TAIL_LINES)}"
            )

    def _write(self, text: str) -> None:
        with self.log.open("a", encoding="utf-8") as log:
            log.write(text)

    def _tail(self, count: int) -> str:
        lines = self.log.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-count:])
