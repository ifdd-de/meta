"""The local Git server for the flows of wf (setting ``wf_git: local``).

``create`` puts a Compose project into ``<workdir>/git`` with its own ``.env``
(0600, with a generated secret key). ``up`` starts it after the proxy and
before wf and then, if missing, creates the user and the repository of
``ifdd_wf_git_url`` (with a first commit on ``ifdd_wf_git_branch``) and a token
that goes into ``wf/secrets/kestra.env`` as ``SECRET_GIT_TOKEN``. Tokens and
passwords never appear on the console, in a log or in a command line.
"""

from __future__ import annotations

import base64
import json
import secrets
import subprocess
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from . import copysecret
from .runner import Run, StepError

DIRECTORY = "git"
#: Upstream image (Gitea, rootless); checked against the v1.27.3 sources.
IMAGE = "gitea/gitea:1.27.3-rootless"
SERVICE = "gitea"
#: The token of wf. write:user as well, because the whole /user group of the API
#: (and so POST /user/repos) requires the user scope (routers/api/v1/api.go, v1.27.3).
#: The user owns nothing but the repository of the flows.
TOKEN_SCOPES = "write:repository,write:user"


def prepare(workdir: Path, installation: str, host: str, network: str, run: Run) -> Path:
    """Write the Compose project and its .env; keeps an existing secret key."""
    target = workdir / DIRECTORY
    target.mkdir(exist_ok=True)
    source = files("ifdd_meta").joinpath("data/git/docker-compose.yml")
    (target / "docker-compose.yml").write_text(source.read_text(encoding="utf-8"))
    env = target / ".env"
    key = None
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("IFDD_GIT_SECRET_KEY="):
                key = line.split("=", 1)[1]
    key = key or secrets.token_urlsafe(48)
    env.touch(mode=0o600)
    env.chmod(0o600)
    env.write_text(
        "# Written by 'ifdd-meta create'; holds a secret, never put it into Git.\n"
        f"IFDD_GIT_PROJECT={installation}-git\n"
        f"IFDD_GIT_IMAGE={IMAGE}\n"
        f"IFDD_GIT_HOST={host}\n"
        f"IFDD_PROXY_NETWORK={network}\n"
        f"IFDD_GIT_SECRET_KEY={key}\n",
        encoding="utf-8",
    )
    run.overview(f"Git server for wf: {target} (https://{host}/)")
    return target


def repository(url: str) -> tuple[str, str]:
    """(owner, name) of an https URL like https://git/owner/name.git."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) != 2:
        raise StepError(f"ifdd_wf_git_url must be https://<host>/<owner>/<name>.git: {url}")
    return parts[0], parts[1].removesuffix(".git")


def provision(path: Path, compose: list[str], context: dict, kestra_env: Path, run: Run) -> None:
    """User, token and repository for the wf flows; does nothing that exists already."""
    owner, name = repository(context["ifdd_wf_git_url"])
    username = context["ifdd_wf_git_username"]
    if owner != username:
        raise StepError(
            f"ifdd_wf_git_url belongs to {owner!r}, but ifdd_wf_git_username is {username!r}"
        )
    branch = context["ifdd_wf_git_branch"]
    execute = [*compose, "exec", "-T", SERVICE]

    users = _quiet([*execute, "gitea", "admin", "user", "list"], path)
    if not any(line.split()[1:2] == [username] for line in users.splitlines()[1:]):
        run.overview(f"Git server: creating user {username}")
        _quiet(
            [
                *execute,
                "gitea",
                "admin",
                "user",
                "create",
                "--username",
                username,
                "--email",
                f"{username}@localhost",
                "--random-password",
                "--must-change-password=false",
            ],  # fmt: skip
            path,
        )

    token = current_token(kestra_env)
    if not token:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        token = _quiet(
            [
                *execute,
                "gitea",
                "admin",
                "user",
                "generate-access-token",
                "--username",
                username,
                "--token-name",
                f"wf-{stamp}",
                "--scopes",
                TOKEN_SCOPES,
                "--raw",
            ],  # fmt: skip
            path,
        ).strip()
        if not token:
            raise StepError("Git server: no token returned")
        copysecret.write_env(kestra_env, "GIT_TOKEN", token, kestra=True)
        run.overview("Git server: token for wf written to wf/secrets/kestra.env (GIT_TOKEN)")

    status = _api(execute, path, token, "GET", f"/repos/{owner}/{name}")
    if status == 404:
        body = {"name": name, "private": True, "auto_init": True, "default_branch": branch}
        status = _api(execute, path, token, "POST", "/user/repos", body)
        if status != 201:
            raise StepError(f"Git server: creating {owner}/{name} answered HTTP {status}")
        run.overview(f"Git server: repository {owner}/{name} with a first commit on {branch}")
    elif status != 200:
        raise StepError(f"Git server: reading {owner}/{name} answered HTTP {status}")


def current_token(kestra_env: Path) -> str:
    """The token of wf in wf/secrets/kestra.env; empty if not set yet."""
    for line in kestra_env.read_text(encoding="utf-8").splitlines():
        if line.startswith("SECRET_GIT_TOKEN="):
            value = line.split("=", 1)[1].strip()
            return base64.b64decode(value).decode() if value else ""
    raise StepError(f"SECRET_GIT_TOKEN missing in {kestra_env}")


def _quiet(argv: list[str], cwd: Path, stdin: str | None = None) -> str:
    """Run a command whose output may hold a secret: never logged, never shown."""
    result = subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        command = " ".join(argv[argv.index(SERVICE) + 1 :][:4]) if SERVICE in argv else argv[0]
        raise StepError(f"Git server: '{command} …' failed with exit code {result.returncode}")
    return result.stdout


def _api(execute: list[str], cwd: Path, token: str, method: str, route: str, body=None) -> int:
    """HTTP status of a call to the Gitea API inside the container; the token goes via stdin."""
    script = (
        'curl -sS -o /dev/null -w "%{http_code}" -H @- -X "$0" "http://127.0.0.1:3000/api/v1$1"'
    )
    if body is not None:
        script = (
            'curl -sS -o /dev/null -w "%{http_code}" -H @- -X "$0" '
            '-H "Content-Type: application/json" --data "$2" "http://127.0.0.1:3000/api/v1$1"'
        )
    argv = [*execute, "sh", "-c", script, method, route]
    if body is not None:
        argv.append(json.dumps(body))
    output = _quiet(argv, cwd, stdin=f"Authorization: token {token}\n")
    try:
        return int(output.strip()[-3:])
    except ValueError:
        raise StepError(f"Git server: unexpected answer from the API for {route}") from None
