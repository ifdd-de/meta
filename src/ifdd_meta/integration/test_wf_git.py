"""wf: a flow made in Kestra reaches the Git server and leaves it again.

Needs the local Git server (setting wf_git: local) and production, where the
Git synchronisation runs. Kestra's API is called inside the kestra container
with Kestra's own login, as wf's git_sync.py does.
"""

import uuid

import pytest

from ifdd_meta import gitserver

#: Runs in the kestra container: python3 -c SCRIPT METHOD PATH [YAML]; prints the HTTP status.
SCRIPT = """
import base64, os, sys, urllib.error, urllib.request
login = os.environ["IFDD_WF_ADMIN_USER"] + ":" + os.environ["IFDD_WF_ADMIN_PASSWORD"]
call = urllib.request.Request(
    "http://localhost:8080/api/v1/main" + sys.argv[2],
    data=sys.argv[3].encode() if len(sys.argv) > 3 else None,
    method=sys.argv[1],
    headers={"Authorization": "Basic " + base64.b64encode(login.encode()).decode(),
             "Content-Type": "application/x-yaml"},
)
try:
    with urllib.request.urlopen(call, timeout=60) as answer:
        print(answer.status)
except urllib.error.HTTPError as error:
    print(error.code)
"""


@pytest.fixture(scope="module")
def wf(installation, writes):
    if not installation.has("wf", "git"):
        pytest.skip("needs wf and the local Git server (wf_git: local)")
    if installation.settings["environment"] != "production":
        pytest.skip("the Git synchronisation runs in production only")
    return installation


def kestra(wf, method: str, path: str, body: str | None = None) -> int:
    argv = ["exec", "-T", "kestra", "python3", "-c", SCRIPT, method, path]
    return int(wf.compose("wf", *argv, *([body] if body else [])).strip())


def flow_files(wf, http, namespace: str) -> list[str]:
    """Names in <namespace>/flows of the content repository (empty if there is none)."""
    variables = wf.variables
    owner, name = gitserver.repository(variables["ifdd_wf_git_url"])
    token = gitserver.current_token(wf.paths["wf"] / "secrets" / "kestra.env")
    answer = http.get(
        f"https://{wf.settings['git_host']}/api/v1/repos/{owner}/{name}/contents/{namespace}/flows",
        params={"ref": variables["ifdd_wf_git_branch"]},
        headers={"Authorization": f"token {token}"},
    )
    if answer.status_code == 404:
        return []
    assert answer.status_code == 200, answer.text
    return [entry["name"] for entry in answer.json()]


def test_flow_round_trip(wf, http):
    namespace = wf.variables["ifdd_wf_git_namespaces"].split(",")[0].strip()
    flow = f"ifdd_meta_test_{uuid.uuid4().hex[:8]}"
    source = (
        f"id: {flow}\nnamespace: {namespace}\n"
        "tasks:\n  - id: hello\n    type: io.kestra.plugin.core.log.Log\n"
        "    message: written by the ifdd-meta integration tests\n"
    )
    assert kestra(wf, "POST", "/flows", source) == 200
    try:
        wf.run("wf", "./adm_03_git_push.sh")
        assert any(flow in file for file in flow_files(wf, http, namespace))
    finally:
        assert kestra(wf, "DELETE", f"/flows/{namespace}/{flow}") == 204
        wf.run("wf", "./adm_03_git_push.sh")
    assert not any(flow in file for file in flow_files(wf, http, namespace))
