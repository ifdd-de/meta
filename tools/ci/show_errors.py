#!/usr/bin/env python3
"""Print the error lines of container logs, with every generated secret masked.

For a failed CI run: the job log is easier to reach than the artefacts.

    show_errors.py WORKDIR LOGDIR
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from scrub_secrets import secret_values

PATTERN = re.compile(r"ERROR|Exception|FAILED|WARN|refused|denied|certificate|PKIX|not found", re.I)
LINES = 60


def main(workdir: Path, logdir: Path) -> int:
    values = sorted(secret_values(workdir), key=len, reverse=True)
    for path in sorted(logdir.glob("*.log")):
        hits = [line for line in path.read_text(errors="replace").splitlines() if PATTERN.search(line)]
        if not hits:
            continue
        print(f"::group::{path.name}: {len(hits)} lines, the last {min(len(hits), LINES)}")
        for line in hits[-LINES:]:
            for value in values:
                line = line.replace(value, "***")
            print(line[:400])
        print("::endgroup::")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
