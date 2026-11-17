#!/usr/bin/env python3
"""Remove files that contain a generated secret from a directory of CI artefacts.

The artefacts of a public repository are public. The secrets of a CI run are
thrown away with it, but they still must not be published. Collects the values
of every ``*SECRET*``, ``*PASSWORD*``, ``*TOKEN*`` and ``*KEY*`` variable in the
``.env`` files and every file in ``secrets/`` of the instances, then deletes
each artefact file that contains one of them. Prints names, never values.

    scrub_secrets.py WORKDIR ARTEFACTS
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|KEY)[A-Z0-9_]*)=(.*)$")
MIN_LENGTH = 8


def secret_values(workdir: Path) -> set[str]:
    values: set[str] = set()
    for env in list(workdir.glob("*/.env")) + list(workdir.glob("*/secrets/*.env")):
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            match = NAME.match(line)
            if match:
                values.add(match.group(2).strip().strip("'\""))
    for path in workdir.glob("*/secrets/*"):
        if path.is_file() and path.suffix != ".env" and path.name != "README.md":
            values.add(path.read_text(encoding="utf-8", errors="replace").strip())
    return {value for value in values if len(value) >= MIN_LENGTH}


def main(workdir: Path, artefacts: Path) -> int:
    values = secret_values(workdir)
    removed = 0
    for path in sorted(p for p in artefacts.rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(value in text for value in values):
            path.unlink()
            removed += 1
            print(f"removed {path}: it contains a generated secret")
    print(f"checked {len(values)} secret values; removed {removed} files")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
