"""Write the description of a second server for the integration run.

The second server ("dev") runs proxy, be, ui, ts, ts-api and ct and uses
Keycloak (the same realm), schema, type and unit of the first one, like the
stages int and dev use those of prod. It takes the template references of the
first description, the local CA of the first server as a further CA, its own
project names and its own ingest network (both run on one Docker host). The
workflow binds its proxy to 127.0.0.2, the first one to 127.0.0.1.

    python tools/ci/second_server.py FIRST_DESCRIPTION FIRST_CA OUT_DIRECTORY
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

LOCAL = {
    "proxy": None,
    "be": "rest.dev.ci.test",
    "ui": "ui.dev.ci.test",
    "ts": "ts.dev.ci.test",
    "ts-api": None,
    "ct": None,
}
#: external service -> its host label on the first server (hosts.suffix there)
EXTERNAL = {"keycloak": "id", "schema": "schema", "type": "type", "unit": "unit"}
PROJECT_NAMES = {
    "ifdd_proxy_prj_name": "Dev Proxy",
    "ifdd_be_prj_name": "Dev Backend",
    "ifdd_ui_prj_name": "Dev UI",
    "ifdd_ct_prj_name": "Dev CT",
    "ifdd_ts_prj_name": "Dev TS",
    "ifdd_ts_api_prj_name": "Dev TS API",
}


def main(first: Path, first_ca: Path, out: Path) -> None:
    data = yaml.safe_load(first.read_text(encoding="utf-8"))
    suffix = data["hosts"]["suffix"]
    templates = data.get("templates") or {}
    refs = templates.get("refs") or {}

    def block(service: str, host: str | None, external: bool = False) -> str:
        items = []
        if host:
            items.append(f"host: {host}")
        if external:
            items.append("external: true")
        if service in refs:
            items.append(f"ref: {refs[service]}")
        return f"  {service}: {{{', '.join(items)}}}\n"

    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(first_ca, out / "first-ca.crt")
    text = (
        "# Second server of the integration run (tools/ci/second_server.py).\n"
        "installation: dev\n"
        "tls: local-ca\n"
        f"realm: {data.get('realm', 'ifdd')}\n"
        f"templates:\n  ref: {templates.get('ref', 'main')}\n"
        "services:\n"
        + "".join(block(service, host) for service, host in LOCAL.items())
        + "".join(
            block(service, f"{label}.{suffix}", external=True)
            for service, label in EXTERNAL.items()
        )
        + "ca_certs: [first-ca.crt]\n"
        "overrides:\n"
        + "".join(f'  {key}: "{value}"\n' for key, value in PROJECT_NAMES.items())
        + '  ifdd_ts_ingest_network: "dev-ts-ingest"\n'
    )
    (out / "installation.yaml").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
