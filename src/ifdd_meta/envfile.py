"""Read a dotenv file: ``KEY=VALUE`` lines, without evaluating anything.

The format is the subset a POSIX shell and Docker Compose read alike: blank
lines and lines starting with ``#`` are ignored, an optional ``export `` prefix
is dropped, and a value in single or double quotes loses its quotes. Nothing is
expanded, so ``$VAR`` stays literal.
"""

from __future__ import annotations

import re
from pathlib import Path

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class EnvFileError(ValueError):
    """A line of a dotenv file cannot be read."""


def parse(text: str, source: str = ".env") -> dict[str, str]:
    """Return the variables of a dotenv text; later lines win."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _KEY.fullmatch(key):
            raise EnvFileError(f"{source}:{number}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def read(path: Path) -> dict[str, str]:
    """Return the variables of a dotenv file, or nothing if it does not exist."""
    if not path.is_file():
        return {}
    return parse(path.read_text(encoding="utf-8"), source=str(path))
