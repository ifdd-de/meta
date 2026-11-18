"""ts-api: write and read a sample, the vocabulary check, roles and store patterns.

ifdd-ct may write (not read), ifdd-api-script may read (not write); both have
the pattern ifdd.de. The test client has both roles, but only the pattern of
the ops example.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from .conftest import bearer

STORE = "temperature.ops.example.ifdd.de"
FOREIGN_STORE = "temperature.grid.example.ifdd.de"


@pytest.fixture(scope="module")
def ts(installation):
    if not installation.has("ts-api", "keycloak", "type"):
        pytest.skip("needs ts-api, keycloak and type")
    return installation.variables["ifdd_ts_api_url"]


@pytest.fixture(scope="module")
def sample(installation):
    """sample(unit=..., value=...): a temperature sample of a new object, two minutes old."""
    variables = installation.variables
    temperature = f"{variables['ifdd_type_url']}/climate.ops.example.ifdd.de/temperature"

    def make(unit: str = "DEG_C", value: float = 21.5) -> dict:
        when = datetime.now(UTC) - timedelta(minutes=2)
        return {
            "name": "temperature",
            "ifdd_id": f"{variables['ifdd_be_url']}/products/{uuid.uuid4()}",
            "ifdd_property": temperature,
            "ifdd_unit": f"{variables['ifdd_unit_url']}/unit/{unit}",
            "time": when.isoformat(timespec="milliseconds"),
            "value": value,
        }

    return make


def test_version(http, ts):
    answer = http.get(f"{ts}/version")
    assert answer.status_code == 200, f"HTTP {answer.status_code}"


def test_samples_need_a_token(http, ts):
    answer = http.get(f"{ts}/stores")
    assert answer.status_code == 401, f"HTTP {answer.status_code}"


def test_write_and_read_the_latest(http, tokens, ts, sample, writes):
    body = sample(value=23.25)
    written = http.post(f"{ts}/stores/{STORE}/samples", json=body, headers=bearer(tokens("ct")))
    assert written.status_code == 200, written.text
    assert written.json() == {"written": 1}, written.text
    params = {"ifdd_id": body["ifdd_id"], "ifdd_property": body["ifdd_property"]}
    for _ in range(30):  # VictoriaMetrics makes new samples searchable after a moment
        latest = http.get(
            f"{ts}/stores/{STORE}/samples", params=params, headers=bearer(tokens("api"))
        )
        if latest.status_code != 404:
            break
        time.sleep(1)
    assert latest.status_code == 200, latest.text
    assert latest.json()["value"] == 23.25, latest.text


def test_vocabulary_is_checked(http, tokens, ts, sample, writes):
    url = f"{ts}/stores/{STORE}/samples"
    wrong_unit = http.post(url, json=sample(unit="PERCENT"), headers=bearer(tokens("ct")))
    assert wrong_unit.status_code == 422, wrong_unit.text
    too_hot = http.post(url, json=sample(value=500), headers=bearer(tokens("ct")))
    assert too_hot.status_code == 422, too_hot.text


def test_roles(http, tokens, ts, sample, writes):
    url = f"{ts}/stores/{STORE}/samples"
    reader_writes = http.post(url, json=sample(), headers=bearer(tokens("api")))
    assert reader_writes.status_code == 403, reader_writes.text
    writer_reads = http.get(f"{ts}/stores", headers=bearer(tokens("ct")))
    assert writer_reads.status_code == 403, writer_reads.text


def test_store_needs_its_pattern(http, ts, sample, test_client):
    own = http.post(f"{ts}/stores/{STORE}/samples", json=sample(), headers=bearer(test_client))
    assert own.status_code == 200, own.text
    foreign = http.post(
        f"{ts}/stores/{FOREIGN_STORE}/samples", json=sample(), headers=bearer(test_client)
    )
    assert foreign.status_code == 403, foreign.text
