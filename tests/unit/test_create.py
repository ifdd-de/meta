"""create against small fake templates, with the real cruft."""

import base64
import json
import subprocess
from pathlib import Path

import pytest

from ifdd_meta import create
from ifdd_meta.catalogue import SKIPS
from ifdd_meta.runner import Run, StepError
from ifdd_meta.settings import SERVICES, load

GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.org"]
DIRECTORY = {"be": "backend", "ui": "frontend", "ts": "time-series", "ts-api": "time-series-api"}

# Files of the fake instances: what create reads or writes.
FILES = {
    "proxy": {
        "adm_01_certs.sh": "#!/bin/sh\nset -e\nmkdir -p certs\necho CA > certs/ca.crt\n",
        "secrets/oauth2_proxy_client_secret": "",
    },
    "keycloak": {
        ".env": "IFDD_KEYCLOAK_PROXY_CLIENT_SECRET=proxy1\nIFDD_KEYCLOAK_TS_CLIENT_SECRET=ts1\n"
        "IFDD_KEYCLOAK_CT_CLIENT_SECRET=ct1\nIFDD_KEYCLOAK_API_CLIENT_SECRET=api1\n",
    },
    "be": {"certs/README.md": "x"},
    "ct": {"certs/README.md": "x", ".env": "IFDD_CT_OIDC_CLIENT_SECRET=\n"},
    "ts": {"certs/README.md": "x", "secrets/oidc_client_secret": ""},
    "ts-api": {"certs/README.md": "x"},
    "wf": {"certs/README.md": "x", "secrets/kestra.env": "SECRET_IFDD_API_CLIENT_SECRET=\n"},
}


def make_template(root: Path, service: str) -> None:
    key = service.replace("-", "_")
    repo = root / service
    body = repo / f"{{{{cookiecutter.ifdd_{key}_prj_slug}}}}"
    body.mkdir(parents=True)
    (repo / "cookiecutter.json").write_text(
        json.dumps(
            {
                f"ifdd_{key}_prj_name": DIRECTORY.get(service, service),
                f"ifdd_{key}_prj_slug": f"{{{{ cookiecutter.ifdd_{key}_prj_name }}}}",
            }
        )
    )
    (body / "README.md").write_text(service)
    for name, text in FILES.get(service, {}).items():
        path = body / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if name.endswith(".sh"):
            path.chmod(0o755)
    subprocess.run([*GIT, "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*GIT, "-C", str(repo), "add", "."], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "tag", "v1"], check=True)


@pytest.fixture(scope="module")
def templates(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("templates")
    for service in SERVICES:
        make_template(root, service)
    return root


def settings_for(tmp_path, templates, text=""):
    (tmp_path / "vars.yaml").write_text("default_context: {}\n")
    (tmp_path / "ifdd-meta.yaml").write_text(
        f"variables: vars.yaml\ntemplates:\n  source: {templates}\n  ref: v1\n{text}"
    )
    return load(cwd=tmp_path, environ={})


def run_create(settings, only=None, is_root=lambda: False):
    lines = []
    run = Run(settings["workdir"] / "logs", "create", lines.append)
    state = create.create(settings, run, only, is_root=is_root)
    return state, lines


def test_create_everything(tmp_path, templates):
    settings = settings_for(tmp_path, templates)
    state, lines = run_create(settings)
    work = settings["workdir"]

    assert state == {s: DIRECTORY.get(s, s) for s in SERVICES}
    assert json.loads((work / ".ifdd-meta" / "instances.json").read_text()) == state
    for service, directory in state.items():
        cruft = json.loads((work / directory / ".cruft.json").read_text())
        assert cruft["skip"] == list(SKIPS[service])
        assert cruft["checkout"] == "v1"
    # local CA in every service that calls others through the proxy
    for directory in ("backend", "ct", "time-series", "time-series-api", "wf"):
        assert (work / directory / "certs" / "ca.crt").read_text() == "CA\n"
    assert not (work / "frontend" / "certs").exists()
    # client secrets
    assert (work / "proxy/secrets/oauth2_proxy_client_secret").read_text() == "proxy1"
    assert (work / "time-series/secrets/oidc_client_secret").read_text() == "ts1"
    assert "IFDD_CT_OIDC_CLIENT_SECRET=ct1" in (work / "ct/.env").read_text()
    kestra = (work / "wf/secrets/kestra.env").read_text().strip().split("=", 1)[1]
    assert base64.b64decode(kestra).decode() == "api1"
    assert lines[0] == "==> Creating proxy (v1)"
    assert not any("proxy1" in line or "api1" in line for line in lines)


def test_existing_instances_are_never_overwritten(tmp_path, templates):
    settings = settings_for(tmp_path, templates, "services: [be]\n")
    run_create(settings)
    (settings["workdir"] / "backend" / "README.md").write_text("mine")
    with pytest.raises(create.CreateError, match="instances exist already: be"):
        run_create(settings)
    assert (settings["workdir"] / "backend" / "README.md").read_text() == "mine"


def test_only_and_unknown_service(tmp_path, templates):
    settings = settings_for(tmp_path, templates, "services: [proxy, be, ui]\n")
    state, _ = run_create(settings, only=["ui"])
    assert state == {"ui": "frontend"}
    with pytest.raises(create.CreateError, match="not among the services"):
        run_create(settings, only=["wf"])


def test_root_is_refused_for_keycloak_and_ts(tmp_path, templates):
    settings = settings_for(tmp_path, templates, "services: [keycloak, be]\n")
    with pytest.raises(create.CreateError, match="run as a normal user"):
        run_create(settings, is_root=lambda: True)
    state, _ = run_create(settings, only=["be"], is_root=lambda: True)
    assert state == {"be": "backend"}


def test_lets_encrypt_creates_no_local_ca(tmp_path, templates):
    settings = settings_for(tmp_path, templates, "tls: letsencrypt\nservices: [proxy, be]\n")
    _, lines = run_create(settings)
    assert not (settings["workdir"] / "proxy" / "certs").exists()
    assert not any("local CA" in line for line in lines)


def test_own_certificates_are_left_to_the_operator(tmp_path, templates):
    settings = settings_for(tmp_path, templates, "tls: own\nservices: [proxy, be]\n")
    _, lines = run_create(settings)
    assert not (settings["workdir"] / "backend" / "certs" / "ca.crt").exists()
    assert any("TLS mode own" in line for line in lines)


def test_wheels_are_copied(tmp_path, templates):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "ifdd_be_core-26.11.1-py3-none-any.whl").write_text("wheel")
    settings = settings_for(tmp_path, templates, "services: [be]\nwheels:\n  be-core: dist\n")
    run_create(settings)
    assert (settings["workdir"] / "backend/wheels/ifdd_be_core-26.11.1-py3-none-any.whl").is_file()


def test_missing_wheel_is_an_error(tmp_path, templates):
    (tmp_path / "dist").mkdir()
    settings = settings_for(tmp_path, templates, "services: [be]\nwheels:\n  be-core: dist\n")
    with pytest.raises(create.CreateError, match="no ifdd_be_core-"):
        run_create(settings)


def test_a_failing_template_stops_with_the_log(tmp_path, templates):
    settings_for(tmp_path, templates, "services: [be]\n")
    settings = load(cwd=tmp_path, environ={"IFDD_META_TEMPLATES_REF": "v9"})
    with pytest.raises(StepError, match="failed with exit code") as error:
        run_create(settings)
    assert "last lines of" in str(error.value)
