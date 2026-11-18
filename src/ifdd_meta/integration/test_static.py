"""schema, type, unit: JSON-LD documents, open to every origin."""

import pytest

DOCUMENTS = [
    ("schema", "ifdd_schema_url", "/product.jsonld"),
    ("type", "ifdd_type_url", "/climate.ops.example.ifdd.de/"),
    ("unit", "ifdd_unit_url", "/unit/DEG_C"),
]


@pytest.mark.parametrize(("service", "variable", "path"), DOCUMENTS)
def test_document_and_cors(installation, http, service, variable, path):
    if not installation.has(service):
        pytest.skip(f"no {service} instance")
    url = installation.variables[variable] + path
    answer = http.get(url, headers={"Origin": "https://elsewhere.example.org"})
    assert answer.status_code == 200, url
    assert "@context" in answer.json(), url
    assert answer.headers.get("access-control-allow-origin") == "*", url
