"""ct: the example pipelines load an energy grid into be and time series into ts-api."""

import pytest

from .conftest import bearer

GRID = "pipelines/generate-energy-grid.yaml"
SERIES = "pipelines/generate-time-series.yaml"
#: Documents of generate-energy-grid.yaml: products, functions, locations, organisations.
GRID_DOCUMENTS = 20 + 8 + 5 + 4


@pytest.fixture(scope="module")
def ct(installation, writes):
    if not installation.has("ct", "be", "keycloak", "schema"):
        pytest.skip("needs ct, be, keycloak and schema")
    return installation


def total(http, url, token):
    answer = http.get(url, params={"start": "0", "end": "1"}, headers=bearer(token))
    assert answer.status_code == 200, answer.text
    return int(answer.headers["x-total-count"])


def test_energy_grid(ct, http, tokens):
    nodes = f"{ct.variables['ifdd_be_url']}/nodes/"
    ct.run("ct", "./adm_02_run.sh", "-f", GRID)
    loaded = total(http, nodes, tokens("api"))
    assert loaded >= GRID_DOCUMENTS, f"{loaded} documents"
    ct.run("ct", "./adm_02_run.sh", "-f", GRID)  # same seed: nothing new
    assert total(http, nodes, tokens("api")) == loaded


def test_time_series(ct, http, tokens):
    if not ct.has("ts-api", "type"):
        pytest.skip("needs ts-api and type")
    ct.run("ct", "./adm_02_run.sh", "-f", SERIES)
    stores = http.get(f"{ct.variables['ifdd_ts_api_url']}/stores", headers=bearer(tokens("api")))
    assert stores.status_code == 200, stores.text
    # Only ct writes this store here.
    assert "humidity.ops.example.ifdd.de" in stores.json(), stores.text
