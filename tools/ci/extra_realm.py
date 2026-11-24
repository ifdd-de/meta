"""Write the values file of a further realm for the integration run.

Like the private values_<stage>.toml files of a Keycloak that serves several
stages: the template's realm/values.toml, rendered with the variables of the
installation, with another realm name. The workflow lists the file in files:
of the description, so create copies it to keycloak/realm/ and runs
build_realm.py, and Keycloak imports the realm at its first start.

    python tools/ci/extra_realm.py SETTINGS REALM OUT_FILE
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ifdd_meta import settings as meta_settings
from ifdd_meta.templates import location

#: The name and title in the [realm] table of values.toml.
REALM_KEYS = re.compile(r'^(name|display_name) = ".*"$', re.MULTILINE)


def main(settings_file: Path, realm: str, out: Path) -> None:
    loaded = meta_settings.load(settings_file=settings_file, environ={})
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "cookiecutter",
                "--no-input",
                "--accept-hooks",
                "no",
                "--checkout",
                loaded.ref("keycloak"),
                "--config-file",
                str(loaded["variables"]),
                "--output-dir",
                temp,
                location(loaded["templates.source"], "keycloak"),
            ],
            check=True,
        )
        (instance,) = Path(temp).iterdir()
        text = (instance / "realm" / "values.toml").read_text(encoding="utf-8")
    head, table, rest = text.partition("[realm]\n")
    section, next_table, tail = rest.partition("\n[")
    values = {"name": realm, "display_name": f"IFDD {realm}"}
    section, count = REALM_KEYS.subn(lambda m: f'{m[1]} = "{values[m[1]]}"', section)
    if not table or count != 2:
        sys.exit(f"realm/values.toml: [realm] name and display_name not found ({count})")
    out.write_text(head + table + section + next_table + tail, encoding="utf-8")
    print(f"{out}: values of realm {realm!r}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
