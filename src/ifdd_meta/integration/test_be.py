"""be: authentication, writes, valid time, context patterns, CORS.

The write tests use the example contexts of the schema service: the domain
context of the grid example (pattern ifdd.de of ifdd-ct, not of the test
client) and the one of the ops example (pattern of the test client).
"""

import uuid

import pytest

from .conftest import bearer

FOREIGN = "breaker.grid.example.ifdd.de"
OWN = "maintenance.ops.example.ifdd.de"
VALID_FROM = "2026-01-01T00:00:00Z"


@pytest.fixture(scope="module")
def be(installation):
    if not installation.has("be", "keycloak", "schema"):
        pytest.skip("needs be, keycloak and schema")
    return installation.variables["ifdd_be_url"]


@pytest.fixture(scope="module")
def document(installation):
    """document(node, context, **fields): the PUT body of a product."""
    variables = installation.variables

    def make(node: str, context: str = "product", **fields) -> dict:
        return {
            "aspect": "product",
            "json": {
                "@context": f"{variables['ifdd_schema_url']}/{context}.jsonld",
                "@id": f"{variables['ifdd_be_url']}/products/{node}",
                **fields,
            },
        }

    return make


@pytest.fixture(scope="module")
def product(http, tokens, be, document, writes):
    """A product by ifdd-ct, valid from VALID_FROM, plus a foreign domain context."""
    node = str(uuid.uuid4())
    name = f"ifdd-meta test {node}"
    base = document(node, objName=name, objType="breaker", c_validNotBefore=VALID_FROM)
    created = http.put(f"{be}/products/{node}", json=base, headers=bearer(tokens("ct")))
    assert created.status_code == 201, created.text
    domain = document(node, FOREIGN, objName=name, serialNumber="SN-TEST")
    created = http.put(f"{be}/products/{node}", json=domain, headers=bearer(tokens("ct")))
    assert created.status_code == 201, created.text
    return node, name, base


def test_version_is_open(http, be):
    answer = http.get(f"{be}/version")
    assert answer.status_code == 200, f"HTTP {answer.status_code}"


def test_data_needs_a_token(http, be):
    answer = http.get(f"{be}/products/{uuid.uuid4()}")
    assert answer.status_code == 401, f"HTTP {answer.status_code}"


def test_write_and_read(http, tokens, be, product):
    node, name, _ = product
    answer = http.get(f"{be}/products/{node}", headers=bearer(tokens("api")))
    assert answer.status_code == 200, answer.text
    assert name in answer.text and FOREIGN in answer.text, answer.text[:500]


def test_unchanged_write_stores_nothing(http, tokens, be, product):
    node, _, base = product
    again = http.put(f"{be}/products/{node}", json=base, headers=bearer(tokens("ct")))
    assert again.status_code == 200, f"HTTP {again.status_code}: {again.text[:300]}"
    assert again.json()["message"] == "No changes detected", again.text


def test_valid_time(http, tokens, be, product):
    node, _, _ = product
    url = f"{be}/products/{node}"
    before = http.get(url, params={"at": "2025-06-01T00:00:00Z"}, headers=bearer(tokens("api")))
    assert before.status_code == 404, f"HTTP {before.status_code}: {before.text[:300]}"
    after = http.get(url, params={"at": "2026-06-01T00:00:00Z"}, headers=bearer(tokens("api")))
    assert after.status_code == 200, f"HTTP {after.status_code}: {after.text[:300]}"


def test_foreign_domain_context_is_hidden(http, be, product, test_client):
    node, name, _ = product
    answer = http.get(f"{be}/products/{node}", headers=bearer(test_client))
    assert answer.status_code == 200, answer.text
    assert name in answer.text and FOREIGN not in answer.text, answer.text[:500]


def test_domain_context_needs_its_pattern(http, be, document, product, test_client):
    node, name, _ = product
    own = http.put(
        f"{be}/products/{node}", json=document(node, OWN, objName=name), headers=bearer(test_client)
    )
    assert own.status_code == 201, own.text
    foreign = http.put(
        f"{be}/products/{node}",
        json=document(node, FOREIGN, objName="not allowed"),
        headers=bearer(test_client),
    )
    assert foreign.status_code == 403, foreign.text


def test_list_of_a_domain_context_needs_its_pattern(
    installation, http, tokens, be, product, test_client
):
    node, _, _ = product
    params = {
        "context": f"{installation.variables['ifdd_schema_url']}/{FOREIGN}.jsonld",
        "query": node,
    }
    allowed = http.get(f"{be}/products", params=params, headers=bearer(tokens("api")))
    assert allowed.status_code == 200, allowed.text
    assert node in allowed.text, allowed.text[:500]
    refused = http.get(f"{be}/products", params=params, headers=bearer(test_client))
    assert refused.status_code == 403, refused.text


def test_cors_only_for_the_ui(installation, http, be):
    ui = installation.variables["ifdd_ui_url"]
    preflight = {"Access-Control-Request-Method": "GET"}
    own = http.options(f"{be}/nodes/", headers={"Origin": ui, **preflight})
    assert own.headers.get("access-control-allow-origin") == ui, f"HTTP {own.status_code}"
    other = http.options(
        f"{be}/nodes/", headers={"Origin": "https://elsewhere.example.org", **preflight}
    )
    assert other.headers.get("access-control-allow-origin") is None, "foreign origin allowed"
