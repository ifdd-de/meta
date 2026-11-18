"""The local Git server: Compose project, provisioning and idempotency (fake docker)."""

import base64
import os
import stat

import pytest

from ifdd_meta import gitserver
from ifdd_meta.runner import Run, StepError

# A docker that answers like Gitea inside the container. State in $GITEA_STATE:
# a file per user, the token, a file per repository; every call in $CALLS.
FAKE_DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$CALLS"
case "$*" in
  *"admin user list"*)
    echo "ID   Username   Email   IsActive   IsAdmin   2FA"
    for u in "$GITEA_STATE"/user-*; do
      [ -e "$u" ] && echo "1   ${u##*/user-}   x@y   true   false   false"
    done ;;
  *"admin user create"*)
    touch "$GITEA_STATE/user-$(echo "$*" | sed 's/.*--username \([^ ]*\).*/\1/')"
    echo "generated random password is 'NOT-TO-BE-SHOWN'" ;;
  *"generate-access-token"*)
    echo "tok-from-gitea" > "$GITEA_STATE/token"; echo "tok-from-gitea" ;;
  *" sh -c "*)
    header=$(cat); echo "$header" >> "$GITEA_STATE/headers"
    case "$*" in
      *" POST /user/repos "*) touch "$GITEA_STATE/repo"; printf 201 ;;
      *" GET /repos/"*) if [ -e "$GITEA_STATE/repo" ]; then printf 200; else printf 404; fi ;;
      *) printf 500 ;;
    esac ;;
esac
exit 0
"""

CONTEXT = {
    "ifdd_wf_git_url": "https://git/kestra/flows.git",
    "ifdd_wf_git_username": "kestra",
    "ifdd_wf_git_branch": "main",
}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    (tmp_path / "gitea").mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CALLS", str(tmp_path / "calls"))
    monkeypatch.setenv("GITEA_STATE", str(tmp_path / "gitea"))
    kestra = tmp_path / "wf" / "secrets" / "kestra.env"
    kestra.parent.mkdir(parents=True)
    kestra.write_text("SECRET_OTHER=x\nSECRET_GIT_TOKEN=\n")
    lines: list[str] = []
    run = Run(tmp_path / "logs", "test", lines.append)
    return tmp_path, kestra, run, lines


def test_prepare_writes_compose_and_a_private_env(tmp_path):
    run = Run(tmp_path / "logs", "test", lambda line: None)
    work = tmp_path / "work"
    work.mkdir()
    path = gitserver.prepare(work, "git", "proxy-net", run)
    env = (path / ".env").read_text()
    assert "IFDD_GIT_HOST=git\n" in env and "IFDD_PROXY_NETWORK=proxy-net\n" in env
    assert f"IFDD_GIT_IMAGE={gitserver.IMAGE}\n" in env
    assert stat.S_IMODE((path / ".env").stat().st_mode) == 0o600
    key = [line for line in env.splitlines() if line.startswith("IFDD_GIT_SECRET_KEY=")][0]
    assert len(key) > 40
    assert "gitea:" in (path / "docker-compose.yml").read_text()
    # a second prepare keeps the secret key
    gitserver.prepare(work, "git", "proxy-net", run)
    assert key in (path / ".env").read_text()


def test_provision_creates_user_token_and_repository_once(fake):
    tmp_path, kestra, run, lines = fake
    gitserver.provision(tmp_path, ["docker", "compose"], CONTEXT, kestra, run)

    token = kestra.read_text().splitlines()[1].split("=", 1)[1]
    assert base64.b64decode(token).decode() == "tok-from-gitea"
    assert kestra.read_text().startswith("SECRET_OTHER=x\n")
    assert (tmp_path / "gitea" / "user-kestra").exists()
    assert (tmp_path / "gitea" / "repo").exists()
    assert "Authorization: token tok-from-gitea" in (tmp_path / "gitea" / "headers").read_text()
    # no token or password on the console, in the log or on a command line
    log = run.log.read_text()
    calls = (tmp_path / "calls").read_text()
    for secret in ("tok-from-gitea", "NOT-TO-BE-SHOWN"):
        assert not any(secret in line for line in lines)
        assert secret not in log and secret not in calls
    assert lines == [
        "==> Git server: creating user kestra",
        "==> Git server: token for wf written to wf/secrets/kestra.env (GIT_TOKEN)",
        "==> Git server: repository kestra/flows with a first commit on main",
    ]

    # second run: nothing new
    lines.clear()
    gitserver.provision(tmp_path, ["docker", "compose"], CONTEXT, kestra, run)
    assert lines == []
    assert (tmp_path / "calls").read_text().count("generate-access-token") == 1
    assert (tmp_path / "calls").read_text().count("POST") == 1


def test_owner_must_be_the_user(fake):
    tmp_path, kestra, run, _ = fake
    context = CONTEXT | {"ifdd_wf_git_username": "someone"}
    with pytest.raises(StepError, match="belongs to 'kestra'"):
        gitserver.provision(tmp_path, ["docker", "compose"], context, kestra, run)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://git/kestra/flows.git", ("kestra", "flows")),
        ("https://git.a.example.org/kestra/flows", ("kestra", "flows")),
    ],
)
def test_repository(url, expected):
    assert gitserver.repository(url) == expected


def test_repository_needs_owner_and_name():
    with pytest.raises(StepError, match="owner"):
        gitserver.repository("https://git/flows.git")
