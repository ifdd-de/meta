"""lab: login through Keycloak, the groups of the hub, delegation and the network.

Four temporary users (names of letters and digits only: DockerSpawner escapes
other characters in container names):

- reader: lab users, nodes readers and the context group of the context root.
  Logs in, gets a JupyterLab; in its server ``ifdd_lab`` reads a product of the
  grid example in its domain context (pattern of the context root).
- plain: lab users and nodes readers, no context group. The same request is
  refused (403) - the counter-check that the notebook acts with the rights of
  the person logged in, not with those of a service client.
- outsider: nodes readers only. The hub refuses the login (403).
- admin: lab admins. Only it opens /hub/admin; the reader gets 403.

The checks in the user servers run with ``docker exec`` (in the
Docker-in-Docker daemon of lab) and use the helper of the notebooks. From
there the services are reachable through the proxy, the internet is not
(ifdd_lab_internet n). The renewal of the token is covered by the tests of
ifdd-de/lab, not here.
"""

from __future__ import annotations

import json
import secrets
import uuid

import pytest

from .conftest import bearer
from .test_browser import User, browser, trust_ca  # noqa: F401 (fixture and helpers)

TIMEOUT = 30_000  # ms
#: A first start of a user server: the container from a 2.8 GB image.
SPAWN_TIMEOUT = 180_000  # ms
FOREIGN = "breaker.grid.example.ifdd.de"
#: A host outside the installation; the user servers must not reach it.
OUTSIDE = "pypi.org"

#: Runs in a user server: the helper of the notebooks, then the network.
PROBE = """
import json, socket, sys
import ifdd_lab
args = json.loads(sys.argv[1])
be = ifdd_lab.session()
answer = be.get("/products", params={"context": args["context"], "query": args["node"]})
result = {"status": answer.status_code, "found": args["node"] in answer.text}
result["version"] = be.get("/version").status_code
try:
    socket.create_connection((args["outside"], 443), timeout=10).close()
    result["outside"] = "reached"
except OSError as error:
    result["outside"] = type(error).__name__
print(json.dumps(result))
"""


@pytest.fixture(scope="module")
def lab(installation):
    if not installation.has("lab", "keycloak", "be", "proxy", "schema"):
        pytest.skip("needs lab, keycloak, be, proxy and schema")
    return f"https://{installation.variables['ifdd_lab_host']}"


@pytest.fixture(scope="module")
def lab_users(installation, admin, lab):
    """reader, plain, outsider and admin (see the module docstring); deleted afterwards."""
    v = installation.variables
    suffix = uuid.uuid4().hex[:8]
    readers = v["ifdd_keycloak_group_nodes_readers"]
    lab_group = v["ifdd_keycloak_lab_users_group"]
    groups = {
        "reader": [lab_group, readers, f"ifdd_ctx_{v['ifdd_context_root']}"],
        "plain": [lab_group, readers],
        "outsider": [readers],
        "admin": [v["ifdd_keycloak_lab_admins_group"]],
    }
    users = {role: User(f"metalab{role}{suffix}", secrets.token_urlsafe(18)) for role in groups}
    created = []
    try:
        for role, user in users.items():
            created.append(admin.add_user(user.name, user.password, groups=groups[role]))
        yield users
    finally:
        for user_id in created:
            admin.delete_user(user_id)
        for user in users.values():
            _remove_server(installation, user.name)


def _dind(installation, *argv: str, stdin: str | None = None) -> str:
    """docker in the Docker-in-Docker daemon of lab."""
    return installation.compose("lab", "exec", "-T", "dind", "docker", *argv, stdin=stdin)


def _remove_server(installation, name: str) -> None:
    """The user's container and home volume in the daemon of lab (test data only)."""
    slug = installation.paths["lab"].name
    for argv in (("rm", "-f", f"{slug}-{name}"), ("volume", "rm", "-f", f"{slug}-home-{name}")):
        try:
            _dind(installation, *argv)
        except AssertionError:
            pass  # nothing to remove


def _sign_in(context, lab: str, user: User, next_path: str = ""):
    """Keycloak login started at the hub; returns the page and the answer of the callback."""
    page = context.new_page()
    start = f"{lab}/hub/oauth_login" + (f"?next={next_path}" if next_path else "")
    page.goto(start)
    page.wait_for_selector("#username")
    page.fill("#username", user.name)
    page.fill("#password", user.password)
    with page.expect_response(lambda r: "/hub/oauth_callback" in r.url) as callback:
        page.click("#kc-login")
    return page, callback.value


@pytest.fixture(scope="module")
def sessions(browser, lab):  # noqa: F811 (fixture imported above)
    """sessions(): a fresh browser context; all are closed afterwards."""
    opened = []

    def new():
        context = browser.new_context(locale="en-US")
        context.set_default_timeout(TIMEOUT)
        opened.append(context)
        return context

    yield new
    for context in opened:
        context.close()


@pytest.fixture(scope="module")
def servers(lab_users, sessions, lab):
    """The reader and the plain user log in; the hub starts their servers (JupyterLab)."""
    for role in ("reader", "plain"):
        user = lab_users[role]
        page, callback = _sign_in(sessions(), lab, user)
        assert callback.status < 400, f"{role}: callback HTTP {callback.status}"
        page.wait_for_url(f"{lab}/user/{user.name}/lab*", timeout=SPAWN_TIMEOUT)
        page.wait_for_selector(".jp-LabShell", timeout=SPAWN_TIMEOUT)
    return lab_users


@pytest.fixture(scope="module")
def product(installation, http, tokens, writes):
    """A product by ifdd-ct with the domain context of the grid example."""
    v = installation.variables
    node = str(uuid.uuid4())
    for context, fields in (("product", {"objType": "breaker"}), (FOREIGN, {"serialNumber": "S"})):
        document = {
            "aspect": "product",
            "json": {
                "@context": f"{v['ifdd_schema_url']}/{context}.jsonld",
                "@id": f"{v['ifdd_be_url']}/products/{node}",
                "objName": f"ifdd-meta lab test {node}",
                "c_validNotBefore": "2026-01-01T00:00:00Z",
                **fields,
            },
        }
        created = http.put(
            f"{v['ifdd_be_url']}/products/{node}", json=document, headers=bearer(tokens("ct"))
        )
        assert created.status_code == 201, created.text
    return node


def _probe(installation, user: User, node: str) -> dict:
    """PROBE in the user's server, as a notebook of that user would run it."""
    v = installation.variables
    args = json.dumps(
        {
            "context": f"{v['ifdd_schema_url']}/{FOREIGN}.jsonld",
            "node": node,
            "outside": OUTSIDE,
        }
    )
    container = f"{installation.paths['lab'].name}-{user.name}"
    output = _dind(installation, "exec", "-i", container, "python", "-", args, stdin=PROBE)
    return json.loads(output.strip().splitlines()[-1])


def test_lab_lets_in_its_group_and_starts_jupyterlab(servers):
    """Covered by the fixture: both users got a JupyterLab after the login."""
    assert set(servers) >= {"reader", "plain"}


def test_lab_refuses_a_user_without_its_group(lab_users, sessions, lab):
    _, callback = _sign_in(sessions(), lab, lab_users["outsider"])
    assert callback.status == 403, f"HTTP {callback.status}"


def test_lab_admin_page_only_for_its_admins(lab_users, sessions, lab):
    page, callback = _sign_in(sessions(), lab, lab_users["admin"], "/hub/admin")
    assert callback.status < 400, f"callback HTTP {callback.status}"
    admin_page = page.goto(f"{lab}/hub/admin")
    assert admin_page.status == 200, f"admin: HTTP {admin_page.status}"
    page, callback = _sign_in(sessions(), lab, lab_users["reader"], "/hub/home")
    refused = page.goto(f"{lab}/hub/admin")
    assert refused.status == 403, f"reader: HTTP {refused.status}"


def test_notebook_reads_with_the_rights_of_the_user(installation, servers, product):
    result = _probe(installation, servers["reader"], product)
    assert result["status"] == 200 and result["found"], result


def test_notebook_of_a_user_without_the_pattern_is_refused(installation, servers, product):
    result = _probe(installation, servers["plain"], product)
    assert result["status"] == 403, result


def test_user_server_reaches_the_services_but_not_the_internet(installation, servers, product):
    result = _probe(installation, servers["reader"], product)
    assert result["version"] == 200, result
    assert result["outside"] != "reached", result
