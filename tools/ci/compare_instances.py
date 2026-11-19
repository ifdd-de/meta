"""Compare two work directories of ifdd-meta: updated instances against fresh ones.

    python tools/ci/compare_instances.py UPDATED FRESH [--known INSTANCE/PATH ...]

For every instance (a directory with a Git repository) the tracked files must
be the same, ``.cruft.json`` compared as data without the template location,
and the ``.env`` files must have the same names with the same values; values
of names with SECRET, PASSWORD, TOKEN or KEY are not compared and never shown.
``--known`` lists differences that are expected (with a reason in the
workflow); they are reported but do not fail. Exit code 1 on other differences.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SECRET = re.compile(r"SECRET|PASSWORD|TOKEN|KEY", re.IGNORECASE)
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def tracked(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(path), "ls-files", "-z"], capture_output=True, text=True, check=True
    )
    return sorted(name for name in result.stdout.split("\0") if name)


def env(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ENV_LINE.match(line)
            if match:
                name = match.group(1)
                values[name] = "<not compared>" if SECRET.search(name) else match.group(2)
    return values


def compare(updated: Path, fresh: Path) -> list[str]:
    """Differences as 'instance/path: what'."""
    found = []
    for instance in sorted(d.name for d in fresh.iterdir() if (d / ".git").is_dir()):
        a, b = updated / instance, fresh / instance
        if not (a / ".git").is_dir():
            found.append(f"{instance}: missing in {updated}")
            continue
        files_a, files_b = tracked(a), tracked(b)
        for name in sorted(set(files_a) ^ set(files_b)):
            found.append(f"{instance}/{name}: only in {'updated' if name in files_a else 'fresh'}")
        for name in sorted(set(files_a) & set(files_b)):
            if name == ".cruft.json":
                data_a = json.loads((a / name).read_text(encoding="utf-8"))
                data_b = json.loads((b / name).read_text(encoding="utf-8"))
                data_a.pop("template", None)
                data_b.pop("template", None)
                if data_a != data_b:
                    found.append(f"{instance}/{name}: differs")
            elif (a / name).read_bytes() != (b / name).read_bytes():
                found.append(f"{instance}/{name}: differs")
        env_a, env_b = env(a / ".env"), env(b / ".env")
        for key in sorted(k for k in set(env_a) | set(env_b) if env_a.get(k) != env_b.get(k)):
            found.append(f"{instance}/.env: {key}: {env_a.get(key)!r} != {env_b.get(key)!r}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("updated", type=Path)
    parser.add_argument("fresh", type=Path)
    parser.add_argument("--known", action="append", default=[], metavar="INSTANCE/PATH")
    options = parser.parse_args(argv)
    failed = False
    for line in compare(options.updated, options.fresh):
        known = any(line.startswith(f"{prefix}:") for prefix in options.known)
        print(f"{'known' if known else 'DIFFERENT'}: {line}")
        failed = failed or not known
    print("updated instances differ from fresh ones" if failed else "same as a fresh create")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
