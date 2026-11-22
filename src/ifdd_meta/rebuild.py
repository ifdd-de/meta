"""``ifdd-meta rebuild``: tear an installation down and build it again.

Meant for a server that is rebuilt from a release at regular intervals, such
as the public demo (a cron job calls it once a week). One run:

1. ``down --purge`` if there are instances (the persist paths stay, e.g.
   ``acme.json`` of Let's Encrypt, so no new certificates are requested);
2. ``create``, ``build``, ``up``;
3. optionally ``demo load`` (``--demo``) and the integration tests
   (``--test``, with the setting ``tests``);
4. optionally ``--prune``: the Docker build cache after ``build`` and every
   image that no container uses after ``up``. Only on a host that runs
   nothing else.

Every run writes ``rebuild-status.txt`` into the persist directory: start,
end, result and, after a failure, the step and the log. ``down --purge``
keeps that directory, so the file shows the last run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from . import create, demo, services
from .create import CreateError, load_state
from .runner import Run
from .settings import Settings

STATUS_FILE = "rebuild-status.txt"

Step = Callable[[Settings, Run], None]


class RebuildError(CreateError):
    """A step of rebuild failed without an exception of its own (the tests)."""


def _down(settings: Settings, run: Run) -> None:
    if not load_state(settings):
        run.overview(f"No instances in {settings['workdir']}: nothing to tear down")
        return
    services.down(settings, run, purge=True)


def _prune_build_cache(settings: Settings, run: Run) -> None:
    run.overview("Deleting the Docker build cache")
    run.command(["docker", "builder", "prune", "--all", "--force"], cwd=settings["workdir"])


def _prune_images(settings: Settings, run: Run) -> None:
    run.overview("Deleting every image that no container uses")
    run.command(["docker", "image", "prune", "--all", "--force"], cwd=settings["workdir"])


DEFAULT_STEPS: Mapping[str, Step] = {
    "down": _down,
    "create": lambda settings, run: create.create(settings, run),
    "build": lambda settings, run: services.build(settings, run),
    "prune-build-cache": _prune_build_cache,
    "up": lambda settings, run: services.up(settings, run),
    "prune-images": _prune_images,
    "demo": demo.load,
}


def plan(prune: bool = False, demo_data: bool = False, test: bool = False) -> list[str]:
    """The names of the steps in their order."""
    names = ["down", "create", "build"]
    if prune:
        names.append("prune-build-cache")
    names.append("up")
    if prune:
        names.append("prune-images")
    if demo_data:
        names.append("demo")
    if test:
        names.append("test")
    return names


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_path(settings: Settings) -> Path:
    return settings["persist"] / STATUS_FILE


def _write_status(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def rebuild(
    settings: Settings,
    run: Run,
    names: list[str],
    tests: Callable[[], int] | None = None,
    steps: Mapping[str, Step] = DEFAULT_STEPS,
) -> None:
    """Run the steps in order; the first failure stops the run.

    ``tests`` runs the integration tests and returns pytest's exit code; it is
    needed when ``names`` contains ``test``.
    """
    status = status_path(settings)
    started = [f"started: {_now()}", f"log: {run.log}", f"steps: {' '.join(names)}"]
    _write_status(status, [*started, "result: running"])
    current = ""
    try:
        for current in names:
            if current == "test":
                if tests is None:
                    raise RebuildError("the step test needs the tests")
                run.overview("Running the integration tests")
                code = tests()
                if code != 0:
                    raise RebuildError(f"the integration tests failed (exit code {code})")
            else:
                steps[current](settings, run)
    except Exception as error:
        first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
        _write_status(
            status,
            [*started, f"finished: {_now()}", "result: failed", f"step: {current}",
             f"error: {first_line}"],
        )  # fmt: skip
        raise
    _write_status(status, [*started, f"finished: {_now()}", "result: ok"])
