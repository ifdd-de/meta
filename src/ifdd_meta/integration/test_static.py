"""schema, type, unit: JSON-LD documents; model: LinkML sources. All open to every origin."""

import pytest

DOCUMENTS = [
    ("schema", "ifdd_schema_url", "/product.jsonld"),
    ("type", "ifdd_type_url", "/climate.ops.example.ifdd.de/"),
    ("unit", "ifdd_unit_url", "/unit/DEG_C"),
]
#: A LinkML source of the model examples (src/linkml), below /v1/ of its host.
MODEL_SOURCE = "/v1/type/product.yaml"


@pytest.mark.parametrize(("service", "variable", "path"), DOCUMENTS)
def test_document_and_cors(installation, http, service, variable, path):
    if not installation.has(service):
        pytest.skip(f"no {service} instance")
    url = installation.variables[variable] + path
    answer = http.get(url, headers={"Origin": "https://elsewhere.example.org"})
    assert answer.status_code == 200, url
    assert "@context" in answer.json(), url
    assert answer.headers.get("access-control-allow-origin") == "*", url


def test_model_source_as_yaml_with_cors(installation, http):
    """model (26.11.6 and later): the LinkML sources as application/yaml (RFC 9512)."""
    if not installation.has("model"):
        pytest.skip("no model instance")
    url = f"https://{installation.variables['ifdd_model_host']}{MODEL_SOURCE}"
    answer = http.get(url, headers={"Origin": "https://elsewhere.example.org"})
    assert answer.status_code == 200, url
    assert answer.headers.get("content-type", "").startswith("application/yaml"), url
    assert answer.headers.get("access-control-allow-origin") == "*", url
    assert "\nname: product\n" in answer.text, url  # the LinkML schema itself
