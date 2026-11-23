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
        "IFDD_KEYCLOAK_CT_CLIENT_SECRET=ct1\nIFDD_KEYCLOAK_API_CLIENT_SECRET=api1\n"
        "IFDD_KEYCLOAK_LAB_CLIENT_SECRET=lab1\n",
    },
    "be": {"certs/README.md": "x"},
    "ct": {
        "certs/README.md": "x",
        ".env": "IFDD_CT_OIDC_CLIENT_SECRET=\nIFDD_CT_CA_BUNDLE=/certs/ca.crt\n",
    },
    "ts": {"certs/README.md": "x", "secrets/oidc_client_secret": ""},
    "ts-api": {"certs/README.md": "x"},
    "wf": {"certs/README.md": "x", "secrets/kestra.env": "SECRET_IFDD_API_CLIENT_SECRET=\n"},
    "lab": {"certs/README.md": "x", "secrets/README.md": "x"},
}


#: Like the templates: secrets, generated configuration and certificates stay out of Git.
GITIGNORE = ".env\nsecrets/*\n!secrets/.gitkeep\ncerts/*\n!certs/README.md\n"


def make_template(root: Path, service: str, gitignore: str = GITIGNORE) -> None:
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
    if gitignore:
        (body / ".gitignore").write_text(gitignore)
    for name, text in FILES.get(service, {}).items():
        path = body / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if name.endswith(".sh"):
            path.chmod(0o755)
    subprocess.run([*GIT, "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*GIT, "-C", str(repo), "add", "--force", "."], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "tag", "v1"], check=True)


@pytest.fixture(autouse=True)
def git_identity(tmp_path_factory, monkeypatch):
    """A global Git configuration with name and e-mail, as on the operator's machine."""
    config = tmp_path_factory.mktemp("git") / "config"
    config.write_text("[user]\n\tname = Test\n\temail = test@example.org\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return config


def git(path: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=path, capture_output=True, text=True, check=True
    ).stdout


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
    # lab only on request (OPTIONAL_SERVICES): named here to create every service.
    settings = settings_for(tmp_path, templates, f"services: [{', '.join(SERVICES)}]\n")
    state, lines = run_create(settings)
    work = settings["workdir"]

    assert state == {s: DIRECTORY.get(s, s) for s in SERVICES}
    assert json.loads((work / ".ifdd-meta" / "instances.json").read_text()) == state
    for service, directory in state.items():
        cruft = json.loads((work / directory / ".cruft.json").read_text())
        assert cruft["skip"] == list(SKIPS[service])
        assert cruft["checkout"] == "v1"
    # local CA in every service that calls others through the proxy
    for directory in ("backend", "ct", "time-series", "time-series-api", "wf", "lab"):
        assert (work / directory / "certs" / "ca.crt").read_text() == "CA\n"
    assert not (work / "frontend" / "certs").exists()
    # client secrets
    assert (work / "proxy/secrets/oauth2_proxy_client_secret").read_text() == "proxy1"
    assert (work / "time-series/secrets/oidc_client_secret").read_text() == "ts1"
    assert "IFDD_CT_OIDC_CLIENT_SECRET=ct1" in (work / "ct/.env").read_text()
    lab_secret = work / "lab/secrets/oidc_client_secret"
    assert lab_secret.read_text() == "lab1"
    assert oct(lab_secret.stat().st_mode & 0o777) == "0o600"
    kestra = (work / "wf/secrets/kestra.env").read_text().strip().split("=", 1)[1]
    assert base64.b64decode(kestra).decode() == "api1"
    assert lines[0] == "==> Creating proxy (v1)"
    assert not any("proxy1" in line or "api1" in line or "lab1" in line for line in lines)
    # every instance is a local Git repository: one commit, clean, no remote
    for service, directory in state.items():
        path = work / directory
        assert git(path, "log", "--format=%an <%ae>|%s").splitlines() == [
            f"Test <test@example.org>|Create {service} from its template at v1"
        ]
        assert git(path, "status", "--porcelain") == ""
        assert git(path, "remote") == ""
        tracked = git(path, "ls-files").splitlines()
        assert ".cruft.json" in tracked
        assert not [name for name in tracked if create.secret_like(name)]
    assert ".env" not in git(work / "keycloak", "ls-files").splitlines()


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


def test_lets_encrypt_empties_the_ca_file_of_ct(tmp_path, templates):
    # The template points ct at /certs/ca.crt; without a local CA that file is missing
    # and every HTTPS call of ct fails (found on the demo server).
    settings = settings_for(tmp_path, templates, "tls: letsencrypt\nservices: [proxy, ct]\n")
    _, lines = run_create(settings)
    env = (settings["workdir"] / "ct" / ".env").read_text()
    assert "IFDD_CT_CA_BUNDLE=\n" in env
    assert "IFDD_CT_OIDC_CLIENT_SECRET=\n" in env
    assert any("IFDD_CT_CA_BUNDLE emptied" in line for line in lines)


def test_local_ca_keeps_the_ca_file_of_ct(tmp_path, templates):
    settings = settings_for(tmp_path, templates, "services: [proxy, ct]\n")
    run_create(settings)
    work = settings["workdir"]
    assert "IFDD_CT_CA_BUNDLE=/certs/ca.crt\n" in (work / "ct" / ".env").read_text()
    assert (work / "ct" / "certs" / "ca.crt").is_file()


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


def test_without_a_git_identity_nothing_is_created(tmp_path, templates, git_identity):
    git_identity.write_text("")
    settings = settings_for(tmp_path, templates, "services: [be]\n")
    with pytest.raises(create.CreateError, match="git config --global user.name"):
        run_create(settings)
    assert not (settings["workdir"] / "backend").exists()


def test_a_secret_the_gitignore_misses_is_not_committed(tmp_path):
    root = tmp_path / "templates"
    make_template(root, "keycloak", gitignore="")
    settings = settings_for(tmp_path, root, "services: [keycloak]\n")
    with pytest.raises(
        create.CreateError, match=r"keycloak: .*would let these files into Git: \.env"
    ):
        run_create(settings)
    assert not (settings["workdir"] / "keycloak" / ".git").exists()


@pytest.mark.parametrize(
    ("path", "secret"),
    [
        (".env", True),
        ("sub/.env", True),
        ("secrets/oidc_client_secret", True),
        ("secrets/kestra.env", True),
        ("certs/ca.key", True),
        ("letsencrypt/acme.json", True),
        ("secrets/dashboard.htpasswd", True),
        ("secrets/README.md", False),
        ("secrets/.gitkeep", False),
        ("secrets/kestra.env.dist", False),
        ("dotenv.dist", False),
        ("certs/README.md", False),
        ("realm/import/ifdd-realm.json", False),
    ],
)
def test_secret_like_paths(path, secret):
    assert create.secret_like(path) is secret
