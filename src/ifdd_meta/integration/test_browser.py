"""Browser tests (Playwright, Chromium): logins through Keycloak.

Two temporary users cover the checks and their counter-checks:

- reader: groups ifdd_nodes_readers and ifdd_ctx_ifdd.de, realm role readonly.
  The ui shows the aspects and loads a list from be; Grafana lets in a
  Viewer; oauth2-proxy refuses wf (403).
- wf admin: only the wf group. The ui shows a hint and no aspects; Grafana refuses the
  login (no realm role, role_attribute_strict); wf lets in.

Needs ``ifdd-meta[browser]`` and ``python -m playwright install chromium``.
Chromium trusts the local CA through an NSS database in a temporary HOME
(``certutil`` of libnss3-tools); the certificates are checked. Screenshots of
failed tests go to IFDD_META_TEST_SCREENSHOTS; no traces, they would record
the passwords.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

sync_api = pytest.importorskip(
    "playwright.sync_api", reason="browser tests need ifdd-meta[browser]"
)

READERS = ["ifdd_nodes_readers", "ifdd_ctx_ifdd.de"]
#: English text of the ui's hint without a read role (ui 26.11.2; the context is en-US).
NO_READ_ROLE = "without the right to read"
TIMEOUT = 30_000  # ms


@dataclass(frozen=True)
class User:
    name: str
    password: str = ""

    def __repr__(self) -> str:  # never the password
        return f"<User {self.name}>"


@pytest.fixture(scope="module")
def users(installation, admin):
    """The reader and the wf admin, for this module; deleted afterwards."""
    variables = installation.variables
    suffix = uuid.uuid4().hex[:8]
    reader = User(f"ifdd-meta-reader-{suffix}", secrets.token_urlsafe(18))
    wf_admin = User(f"ifdd-meta-wf-{suffix}", secrets.token_urlsafe(18))
    created = [
        admin.add_user(reader.name, reader.password, groups=READERS, realm_roles=["readonly"]),
    ]
    try:
        created.append(
            admin.add_user(
                wf_admin.name, wf_admin.password, groups=[variables["ifdd_wf_sso_group"]]
            )
        )
        yield {"reader": reader, "wf": wf_admin}
    finally:
        for user in created:
            admin.delete_user(user)


@pytest.fixture(scope="module")
def browser(installation, tmp_path_factory):
    if not installation.has("keycloak"):
        pytest.skip("no keycloak instance")
    home = tmp_path_factory.mktemp("browser-home")
    if installation.ca_file:
        trust_ca(home, installation.ca_file)
    with sync_api.sync_playwright() as playwright:
        chromium = playwright.chromium.launch(env={**os.environ, "HOME": str(home)})
        yield chromium
        chromium.close()


def trust_ca(home: Path, ca_file: Path) -> None:
    """A private NSS database with the local CA; Chromium on Linux reads $HOME/.pki/nssdb."""
    if not shutil.which("certutil"):
        pytest.fail("certutil missing (Debian/Ubuntu: libnss3-tools); needed for the local CA")
    database = home / ".pki" / "nssdb"
    database.mkdir(parents=True)
    for argv in (
        ["-N", "--empty-password"],
        ["-A", "-t", "C,,", "-n", "ifdd-meta local CA", "-i", str(ca_file)],
    ):
        subprocess.run(["certutil", "-d", f"sql:{database}", *argv], check=True)


@pytest.fixture
def page(browser, request):
    """A page in a fresh context (no session); a screenshot if the test failed."""
    context = browser.new_context(locale="en-US")
    context.set_default_timeout(TIMEOUT)
    new = context.new_page()
    yield new
    report = getattr(request.node, "report_call", None)
    target = os.environ.get("IFDD_META_TEST_SCREENSHOTS")
    if target and report is not None and report.failed:
        name = re.sub(r"[^\w.-]+", "_", request.node.name)
        Path(target).mkdir(parents=True, exist_ok=True)
        new.screenshot(path=str(Path(target) / f"{name}.png"))
    context.close()


def login(page, start: str, user: User, back_to: str):
    """Open start, sign in on the Keycloak form and wait until back_to is reached."""
    page.goto(start)
    page.wait_for_selector("#username")
    page.fill("#username", user.name)
    page.fill("#password", user.password)
    page.click("#kc-login")
    page.wait_for_url(lambda url: url.startswith(back_to) and "/authentication/" not in url)


@pytest.fixture
def ui(installation):
    if not installation.has("ui", "be"):
        pytest.skip("needs ui and be")
    return installation.variables["ifdd_ui_url"]


def test_ui_reader_sees_and_loads_the_aspects(installation, users, page, ui):
    be = installation.variables["ifdd_be_url"]
    login(page, ui, users["reader"], ui)
    products = page.locator('a[href$="/products"]')
    products.first.wait_for()
    hint = page.get_by_role("status").filter(has_text=NO_READ_ROLE)
    assert hint.count() == 0, "hint without a read role shown to a reader"
    with page.expect_response(lambda r: r.url.startswith(f"{be}/products")) as answer:
        products.first.click()
    assert answer.value.status == 200, f"be answered HTTP {answer.value.status}"


def test_ui_without_read_role_shows_a_hint_and_no_aspects(users, page, ui):
    login(page, ui, users["wf"], ui)
    # The hint appears once the permissions are known, so the menu is complete too.
    page.get_by_role("status").filter(has_text=NO_READ_ROLE).wait_for()
    found = page.locator('a[href$="/products"]').count()
    assert found == 0, "aspects shown without a read role"


@pytest.fixture
def grafana(installation):
    if not installation.has("ts"):
        pytest.skip("no ts instance")
    return f"https://{installation.variables['ifdd_ts_host']}"


def test_grafana_lets_in_a_viewer(users, page, grafana):
    login(page, f"{grafana}/login/generic_oauth", users["reader"], grafana)
    me = page.goto(f"{grafana}/api/user")
    assert me.status == 200, f"HTTP {me.status}"
    assert me.json()["login"] == users["reader"].name, "another user"
    orgs = page.goto(f"{grafana}/api/user/orgs").json()
    assert [org["role"] for org in orgs] == ["Viewer"], f"roles: {orgs}"


def test_grafana_refuses_a_user_without_realm_role(users, page, grafana):
    login(page, f"{grafana}/login/generic_oauth", users["wf"], grafana)
    me = page.goto(f"{grafana}/api/user")
    assert me.status == 401, f"HTTP {me.status}"


@pytest.fixture
def wf(installation):
    if not installation.has("wf", "proxy"):
        pytest.skip("needs wf and proxy")
    if installation.settings["environment"] != "production":
        pytest.skip("oauth2-proxy guards wf in production only")
    return f"https://{installation.variables['ifdd_wf_host']}"


def test_wf_lets_in_its_group(users, page, wf):
    login(page, wf, users["wf"], wf)
    kestra = page.goto(f"{wf}/")
    assert kestra.status == 200, f"HTTP {kestra.status}"


def test_wf_refuses_other_users(users, page, wf):
    login(page, wf, users["reader"], wf)
    refused = page.goto(f"{wf}/")
    assert refused.status == 403, f"HTTP {refused.status}"
