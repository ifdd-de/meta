"""The proxy: security headers and CSP on the routes, the dashboard behind a password."""

import pytest

ROUTES = [
    ("be", "ifdd_be_url", "/version", "ifdd_proxy_csp_api_docs"),
    ("ts-api", "ifdd_ts_api_url", "/version", "ifdd_proxy_csp_api_docs"),
    ("ui", "ifdd_ui_url", "/", "ifdd_proxy_csp_ui"),
    ("schema", "ifdd_schema_url", "/product.jsonld", "ifdd_proxy_csp_api"),
    ("type", "ifdd_type_url", "/climate.ops.example.ifdd.de/", "ifdd_proxy_csp_api"),
    ("unit", "ifdd_unit_url", "/unit/DEG_C", "ifdd_proxy_csp_api"),
]


@pytest.mark.parametrize(("service", "variable", "path", "csp"), ROUTES)
def test_security_headers(installation, http, service, variable, path, csp):
    if not installation.has("proxy", service):
        pytest.skip(f"no proxy or {service} instance")
    variables = installation.variables
    if variables["ifdd_proxy_enable_security_headers"] != "y":
        pytest.skip("security headers are off (ifdd_proxy_enable_security_headers)")
    answer = http.get(variables[variable] + path)
    assert answer.status_code == 200, f"HTTP {answer.status_code}: {answer.text[:300]}"
    expected = {
        "x-content-type-options": "nosniff",
        "x-frame-options": variables["ifdd_proxy_frame_options"],
        "referrer-policy": variables["ifdd_proxy_referrer_policy"],
        "server": None,
        "content-security-policy": variables[csp],
    }
    found = {name: answer.headers.get(name) for name in expected}
    assert found == expected, f"headers: {found}"


def test_dashboard_needs_a_password(installation, http):
    if not installation.has("proxy"):
        pytest.skip("no proxy instance")
    answer = http.get(f"https://{installation.variables['ifdd_proxy_host']}/dashboard/")
    assert answer.status_code == 401, f"HTTP {answer.status_code}: {answer.text[:300]}"
