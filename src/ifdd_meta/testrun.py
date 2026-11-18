"""``ifdd-meta test``: the integration tests of the package, run with pytest.

The tests read the settings file through IFDD_META_TEST_SETTINGS; values given
on the command line of ifdd-meta reach them as IFDD_META_* variables, which
rank directly below the command line, so the tests see the same settings.

Assertions are not rewritten (``--assert=plain``): pytest would otherwise
print the values of an assertion, and these may be tokens. A failure shows
only the message the test gives.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from .settings import FIELDS, Settings

SETTINGS_VARIABLE = "IFDD_META_TEST_SETTINGS"
SCREENSHOTS_VARIABLE = "IFDD_META_TEST_SCREENSHOTS"


def directory() -> Path:
    return Path(str(files("ifdd_meta").joinpath("integration")))


def arguments(
    junit: Path | None = None,
    keyword: str | None = None,
    exitfirst: bool = False,
    verbose: bool = False,
) -> list[str]:
    tests = str(directory())
    args = [
        tests,
        "--rootdir", tests,
        "-c", os.devnull,  # no configuration of the current directory
        "-p", "no:cacheprovider",  # writes nothing into the package or the current directory
        "--assert=plain",
        "--tb=short",
        "-rs",  # list the skipped tests with their reason
    ]  # fmt: skip
    if junit:
        args += ["--junitxml", str(junit)]
    if keyword:
        args += ["-k", keyword]
    if exitfirst:
        args.append("-x")
    if verbose:
        args.append("-v")
    return args


def environment(
    settings: Settings, cli: Mapping[str, Any], screenshots: Path | None = None
) -> dict[str, str]:
    """The variables the tests need: the settings file and the command line values."""
    if settings.file is None:
        raise ValueError("the tests need a settings file (ifdd-meta.yaml or --settings)")
    variables = {SETTINGS_VARIABLE: str(settings.file.resolve())}
    if screenshots:
        variables[SCREENSHOTS_VARIABLE] = str(screenshots.resolve())
    for field in FIELDS:
        if cli.get(field.name) is not None and field.env:
            variables[field.env] = str(settings[field.name])  # resolved, e.g. absolute paths
    return variables


def run(
    settings: Settings, cli: Mapping[str, Any], screenshots: Path | None = None, **options
) -> int:
    """Run the tests; returns pytest's exit code."""
    previous = dict(os.environ)
    os.environ.update(environment(settings, cli, screenshots))
    try:
        return int(pytest.main(arguments(**options)))
    finally:
        os.environ.clear()
        os.environ.update(previous)
