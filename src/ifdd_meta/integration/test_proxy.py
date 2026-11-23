"""The proxy: security headers and CSP on the routes, the dashboard behind a password."""

import socket
from urllib.parse import urlsplit

import pytest

ROUTES = [
    ("be", "ifdd_be_url", "/version", "ifdd_proxy_csp_api_docs"),
    ("ts-api", "ifdd_ts_api_url", "/version", "ifdd_proxy_csp_api_docs"),
    ("ui", "ifdd_ui_url", "/", "ifdd_proxy_csp_ui"),
    ("schema", "ifdd_schema_url", "/product.jsonld", "ifdd_proxy_csp_api"),
    ("type", "ifdd_type_url", "/climate.ops.example.ifdd.de/", "ifdd_proxy_csp_api"),
    ("unit", "ifdd_unit_url", "/unit/DEG_C", "ifdd_proxy_csp_api"),
    ("model", "ifdd_model_host", "/v1/type/product.yaml", "ifdd_proxy_csp_api"),
]


def base_url(variables: dict, variable: str) -> str:
    """A URL variable as it is, a host variable (model has no URL variable) as https://host."""
    value = variables[variable]
    return f"https://{value}" if variable.endswith("_host") else value


@pytest.mark.parametrize(("service", "variable", "path", "csp"), ROUTES)
def test_security_headers(installation, http, service, variable, path, csp):
    if not installation.has("proxy", service):
        pytest.skip(f"no proxy or {service} instance")
    variables = installation.variables
    if variables["ifdd_proxy_enable_security_headers"] != "y":
        pytest.skip("security headers are off (ifdd_proxy_enable_security_headers)")
    answer = http.get(base_url(variables, variable) + path)
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


def status_through(address: str, host: str, path: str, context) -> int:
    """HTTP status of GET https://host/path, sent to address (like curl --resolve)."""
    with socket.create_connection((address, 443), timeout=30) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            tls.sendall(request.encode())
            status_line = tls.recv(256).split(b"\r\n", 1)[0]
    return int(status_line.split()[1])


def test_dashboard_needs_a_password(installation, ssl_context):
    """The dashboard host has no DNS record; it is reached at the address of be."""
    if not installation.has("proxy", "be"):
        pytest.skip("no proxy or be instance")
    if installation.settings["tls"] != "local-ca":
        pytest.skip("only the local CA covers the dashboard host (it has no DNS record)")
    variables = installation.variables
    address = socket.gethostbyname(urlsplit(variables["ifdd_be_url"]).hostname)
    status = status_through(address, variables["ifdd_proxy_host"], "/dashboard/", ssl_context)
    assert status == 401, f"HTTP {status}"
