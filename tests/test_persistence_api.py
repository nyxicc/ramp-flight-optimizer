"""Persistent-resource API and reproducibility tests."""

from fastapi.testclient import TestClient
import pytest

from ramp_optimizer_api.app import create_app
from ramp_optimizer_api.mapping import operational_day_to_request
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.models import Base
from tests.scenario_builders import small_ready_scenario


@pytest.fixture
def client() -> TestClient:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with TestClient(create_app(session_factory=make_session_factory(engine))) as value:
        yield value
    engine.dispose()


def _payload():
    scenario = small_ready_scenario()
    return operational_day_to_request(scenario.day, scenario.config).model_dump(mode="json")


def _without_runtime(value):
    if isinstance(value, dict):
        return {key: _without_runtime(item) for key, item in value.items() if "runtime" not in key}
    if isinstance(value, list):
        return [_without_runtime(item) for item in value]
    return value


def test_persistent_api_reproducibility_and_lists(client: TestClient) -> None:
    payload = _payload()
    created = client.post("/api/v1/operational-days", json=payload)
    assert created.status_code == 201
    day = created.json()
    assert day["input"] == payload
    assert len(day["input_hash"]) == 64

    retrieved = client.get(f"/api/v1/operational-days/{day['id']}")
    assert retrieved.status_code == 200 and retrieved.json() == day
    listing = client.get("/api/v1/operational-days", params={"operational_date": payload["operational_day"]["operational_date"]})
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert "input" not in listing.json()["items"][0]

    direct = client.post("/api/v1/optimizations", json=payload)
    stored = client.post(f"/api/v1/operational-days/{day['id']}/optimizations")
    assert stored.status_code == 201
    run = stored.json()
    assert run["input_hash"] == day["input_hash"]
    assert _without_runtime(run["result"]) == _without_runtime(direct.json())

    loaded_run = client.get(f"/api/v1/optimization-runs/{run['id']}")
    assert loaded_run.json() == run
    runs = client.get(f"/api/v1/operational-days/{day['id']}/optimization-runs")
    assert runs.status_code == 200 and runs.json()["total"] == 1
    assert "result" not in runs.json()["items"][0]


@pytest.mark.parametrize("url", ["/api/v1/operational-days/not-a-uuid", "/api/v1/optimization-runs/not-a-uuid"])
def test_malformed_ids_are_structural_errors(client: TestClient, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_unknown_resources_and_pagination(client: TestClient) -> None:
    unknown = "00000000-0000-0000-0000-000000000099"
    assert client.get(f"/api/v1/operational-days/{unknown}").status_code == 404
    assert client.get(f"/api/v1/optimization-runs/{unknown}").status_code == 404
    assert client.get("/api/v1/operational-days", params={"limit": 101}).status_code == 422


def test_invalid_day_is_not_persisted(client: TestClient) -> None:
    payload = _payload()
    payload["config"]["minimum_staff"] = 0
    response = client.post("/api/v1/operational-days", json=payload)
    assert response.status_code == 422
    assert client.get("/api/v1/operational-days").json()["total"] == 0

