"""ts: Grafana reaches both data sources (VictoriaMetrics and be)."""

import pytest


@pytest.mark.parametrize("uid", ["ifdd-ts-vm", "ifdd-ts-be"])
def test_data_source_health(installation, http, uid):
    if not installation.has("ts"):
        pytest.skip("no ts instance")
    password = (installation.paths["ts"] / "secrets" / "grafana_admin_password").read_text()
    answer = http.get(
        f"https://{installation.variables['ifdd_ts_host']}/api/datasources/uid/{uid}/health",
        auth=("admin", password.strip()),
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "OK", answer.text
