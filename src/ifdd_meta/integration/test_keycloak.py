"""Keycloak: discovery and the client credentials of the services."""

import pytest


def test_discovery(installation, http):
    if not installation.has("keycloak"):
        pytest.skip("no keycloak instance")
    realm = installation.variables["ifdd_oidc_realm"]
    answer = http.get(f"{realm}/.well-known/openid-configuration")
    assert answer.status_code == 200, f"HTTP {answer.status_code}: {answer.text[:300]}"
    assert answer.json()["issuer"] == realm, answer.json()["issuer"]


@pytest.mark.parametrize("client", ["ct", "api", "ingest"])
def test_client_credentials(tokens, client):
    assert tokens(client).count(".") == 2, "not a signed JWT"  # the token is never shown
