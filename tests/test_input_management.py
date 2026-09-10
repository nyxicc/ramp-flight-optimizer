"""Fictional immutable input-management workflows."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ramp_optimizer_api.app import create_app
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.models import Base, OperationalDayRow
from tests.job_helpers import completed_result
from tests.test_persistence_api import _payload, _without_runtime


@pytest.fixture
def context():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with TestClient(create_app(session_factory=sessions)) as client:
        yield client, sessions
    engine.dispose()


def draft(client, key="create"):
    day = _payload()["operational_day"]["operational_date"]
    response = client.post(f"/api/v1/operational-days/{day}/drafts", json={"idempotency_key": key})
    assert response.status_code == 201, response.text
    return response.json()


def revise(client, parent, payload, key="edit"):
    return client.post(
        f"/api/v1/operational-day-versions/{parent['id']}/revisions",
        json={
            "idempotency_key": key,
            "expected_parent_hash": parent["content_hash"],
            "input": payload,
        },
    )


def test_lineage_retries_conflicts_and_reproducibility(context):
    client, _ = context
    first = draft(client)
    assert first["version_number"] == 1 and len(first["warnings"]) == 2
    assert draft(client) == first
    result = revise(client, first, _payload())
    assert result.status_code == 201, result.text
    second = result.json()
    assert second["parent_version_id"] == first["id"]
    assert second["version_number"] == 2 and second["input"] == _payload()
    assert revise(client, first, _payload()).json() == second
    assert revise(client, first, _payload(), "stale").status_code == 409
    assert client.get(f"/api/v1/operational-day-versions/{first['id']}").json() == first
    listing = client.get(f"/api/v1/operational-days/{first['operational_date']}/versions").json()
    assert listing == [first, second]
    direct = client.post("/api/v1/optimizations", json=second["input"])
    direct_result = completed_result(client, direct)
    stored = client.post(f"/api/v1/operational-days/{second['id']}/optimizations")
    assert stored.status_code == 202
    assert _without_runtime(completed_result(client, stored)) == _without_runtime(direct_result)


def test_employee_flight_and_fixed_assignment_operations(context):
    client, _ = context
    parent = revise(client, draft(client), _payload()).json()
    data = deepcopy(parent["input"])
    day = data["operational_day"]
    employee = day["employees"][0]
    for index, enabled in enumerate((False, True)):
        employee["enabled"] = enabled
        result = revise(client, parent, data, f"enable-{index}")
        assert result.status_code == 201, result.text
        parent = result.json()
    for index, quals in enumerate(([], ["PUSH"], ["CLOSE_OUT"], ["PUSH", "CLOSE_OUT"])):
        employee["qualifications"] = quals
        result = revise(client, parent, data, f"qual-{index}")
        assert result.status_code == 201, result.text
        parent = result.json()
    shift = day["employee_shifts"][0]
    shift["start"] = shift["start"].replace("07:00", "06:00")
    shift["end"] = shift["end"].replace("11:00", "12:00")
    flight = day["flights"][0]
    flight["heavy"] = True
    result = revise(client, parent, data, "shift-heavy")
    assert result.status_code == 201, result.text
    parent = result.json()
    day["fixed_assignments"] = [
        {
            "employee_id": employee["employee_id"],
            "flight": {
                "departure_flight_number": flight["departure_flight_number"],
                "arrival_flight_number": None,
            },
        }
    ]
    result = revise(client, parent, data, "fixed")
    assert result.status_code == 201, result.text
    parent = result.json()
    day["fixed_assignments"] = []
    flight["heavy"] = False
    flight["departure_time"] = flight["departure_time"].replace("09:00", "09:15")
    result = revise(client, parent, data, "unfix-edit")
    assert result.status_code == 201, result.text
    parent = result.json()
    day["flights"] = []
    result = revise(client, parent, data, "remove")
    assert result.status_code == 201, result.text
    assert parent["input"]["operational_day"]["flights"]


@pytest.mark.parametrize(
    "kind",
    [
        "unknown_employee",
        "unknown_flight",
        "shift",
        "duplicate",
        "qualification",
        "date",
        "delete_employee",
        "coverage",
        "overlap",
    ],
)
def test_invalid_edits_roll_back(context, kind):
    client, sessions = context
    parent = revise(client, draft(client), _payload()).json()
    data = deepcopy(parent["input"])
    day = data["operational_day"]
    employee = day["employees"][0]["employee_id"]
    ref = {"departure_flight_number": day["flights"][0]["departure_flight_number"]}
    if kind == "unknown_employee":
        day["fixed_assignments"] = [{"employee_id": "FICTIONAL-MISSING", "flight": ref}]
    elif kind == "unknown_flight":
        day["fixed_assignments"] = [
            {"employee_id": employee, "flight": {"departure_flight_number": "FX999"}}
        ]
    elif kind == "shift":
        day["employee_shifts"][0]["end"] = day["employee_shifts"][0]["start"]
    elif kind == "duplicate":
        day["employees"].append(day["employees"][0])
    elif kind == "qualification":
        day["employees"][0]["qualifications"] = ["UNSUPPORTED"]
    elif kind == "date":
        day["operational_date"] = "2000-01-01"
    elif kind == "delete_employee":
        day["employees"].pop()
    elif kind == "coverage":
        day["employees"][0]["enabled"] = False
        day["fixed_assignments"] = [{"employee_id": employee, "flight": ref}]
    else:
        other = deepcopy(day["flights"][0])
        other["departure_flight_number"] = "FX102"
        day["flights"].append(other)
        day["fixed_assignments"] = [
            {"employee_id": employee, "flight": ref},
            {"employee_id": employee, "flight": {"departure_flight_number": "FX102"}},
        ]
    with sessions() as session:
        count = session.scalar(select(func.count()).select_from(OperationalDayRow))
    result = revise(client, parent, data, kind)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["details"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OperationalDayRow)) == count


def test_source_version_inventory_and_key_reuse(context):
    client, _ = context
    first = draft(client)
    path = f"/api/v1/operational-days/{first['operational_date']}/drafts"
    child = client.post(path, json={"idempotency_key": "fork", "source_version_id": first["id"]})
    assert child.status_code == 201 and child.json()["content_hash"] == first["content_hash"]
    assert child.json()["parent_version_id"] == first["id"]
    assert client.post(path, json={"idempotency_key": "fork"}).status_code == 409
    routes = client.get("/openapi.json").json()["paths"]
    assert routes["/api/v1/operational-days/{day}/drafts"]["post"]
    assert routes["/api/v1/operational-days/{day}/versions"]["get"]
    assert routes["/api/v1/operational-day-versions/{version_id}"]["get"]
    assert routes["/api/v1/operational-day-versions/{version_id}/revisions"]["post"]


def test_import_source_and_failed_persistence(context, monkeypatch):
    import ramp_optimizer_api.input_services as module
    from ramp_optimizer import Employee, OptimizerConfig
    from ramp_optimizer_imports.services import ImportService
    from ramp_optimizer_persistence.imports import import_transactions
    from tests.import_fixtures import DAY, workbook

    client, sessions = context
    imports = ImportService(import_transactions(sessions))
    uploaded = imports.upload(
        workbook(),
        "fictional.xlsx",
        "application/octet-stream",
        DAY,
        (Employee("SYN001", "Fictional Avery"),),
        OptimizerConfig(),
    )
    confirmed = imports.confirm(uploaded.import_id, 1)
    result = client.post(
        f"/api/v1/operational-days/{DAY}/drafts",
        json={
            "idempotency_key": "import",
            "source_snapshot_id": confirmed.confirmed_operational_day_id,
        },
    )
    assert result.status_code == 201, result.text
    assert result.json()["input"]["operational_day"]["employees"][0]["employee_id"] == "SYN001"
    with sessions() as session:
        count = session.scalar(select(func.count()).select_from(OperationalDayRow))

    def fail(*args):
        raise RuntimeError("Fictional persistence failure")

    monkeypatch.setattr(module, "append_version", fail)
    with pytest.raises(RuntimeError, match="Fictional persistence failure"):
        draft(client, "rollback")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OperationalDayRow)) == count


def test_concurrent_retries_and_edits(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from ramp_optimizer_api.input_schemas import DraftRequest, RevisionRequest
    from ramp_optimizer_api.input_services import InputService
    from ramp_optimizer_persistence.errors import PersistenceConflictError

    engine = create_database_engine(f"sqlite:///{(tmp_path / 'race.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    service = InputService(make_session_factory(engine))
    from datetime import date

    day = date.fromisoformat(_payload()["operational_day"]["operational_date"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        roots = list(
            pool.map(lambda _: service.create(day, DraftRequest(idempotency_key="same")), range(2))
        )
    assert roots[0] == roots[1]
    parent = roots[0]

    def edit(key):
        try:
            return service.create(
                day,
                RevisionRequest.model_validate(
                    {
                        "idempotency_key": key,
                        "expected_parent_hash": parent.content_hash,
                        "input": _payload(),
                    }
                ),
                str(parent.id),
            )
        except PersistenceConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["left", "right"]))
    assert sum(result is not None for result in results) == 1
    assert [v.version_number for v in service.list(day, 100, 0)] == [1, 2]
    engine.dispose()


def test_orm_immutability(context):
    from ramp_optimizer_persistence.errors import PersistenceIntegrityError
    from ramp_optimizer_persistence.models import EmployeeRow, InputVersionRow

    client, sessions = context
    parent = revise(client, draft(client), _payload()).json()
    for operation in ("metadata", "employee", "delete", "append"):
        with pytest.raises(PersistenceIntegrityError):
            with sessions.begin() as session:
                if operation == "metadata":
                    session.get(InputVersionRow, parent["id"]).reason = "overwrite"
                elif operation == "employee":
                    session.scalar(
                        select(EmployeeRow).where(EmployeeRow.operational_day_id == parent["id"])
                    ).enabled = False
                elif operation == "append":
                    snapshot = session.get(OperationalDayRow, parent["id"])
                    snapshot.employees.append(
                        EmployeeRow(
                            ordinal=99,
                            employee_id="EXTRA",
                            name="Fictional Extra",
                            enabled=True,
                            qualifications_json="[]",
                        )
                    )
                else:
                    session.delete(session.get(InputVersionRow, parent["id"]))
    assert client.get(f"/api/v1/operational-day-versions/{parent['id']}").json() == parent


@pytest.mark.parametrize(
    "change",
    [
        "incomplete_time",
        "duplicate_flight",
        "mixed_category",
        "overstaffed",
        "duplicate_qualification",
        "shift_boundary",
        "flight_boundary",
        "limit",
    ],
)
def test_phase_one_and_boundary_checks(context, change):
    client, _ = context
    parent = revise(client, draft(client), _payload()).json()
    payload = deepcopy(parent["input"])
    day = payload["operational_day"]
    if change == "incomplete_time":
        day["flights"][0]["departure_time"] = None
    elif change == "duplicate_flight":
        day["flights"].append(day["flights"][0])
    elif change == "mixed_category":
        day["flights"][0].update(
            arrival_flight_number="FX9999",
            arrival_time=day["flights"][0]["departure_time"].replace("09:00", "08:00"),
        )
    elif change == "overstaffed":
        for i in range(5):
            day["employees"].append({"employee_id": f"EXTRA{i}", "name": f"Fictional Extra {i}"})
            shift = deepcopy(day["employee_shifts"][0])
            shift["employee_id"] = f"EXTRA{i}"
            day["employee_shifts"].append(shift)
            day["fixed_assignments"].append(
                {
                    "employee_id": f"EXTRA{i}",
                    "flight": {
                        "departure_flight_number": day["flights"][0]["departure_flight_number"]
                    },
                }
            )
    elif change == "duplicate_qualification":
        day["employees"][0]["qualifications"] = ["PUSH", "PUSH"]
    elif change == "shift_boundary":
        day["employee_shifts"][0]["start"] = "2000-01-01T07:00:00"
    elif change == "flight_boundary":
        day["flights"][0]["departure_time"] = "2000-01-01T07:00:00"
    else:
        day["flights"] *= 1001
    result = revise(client, parent, payload, change)
    assert result.status_code == 422, result.text


def test_stored_shortage_warnings_and_qualification_optimization(context):
    client, _ = context
    parent = revise(client, draft(client), _payload()).json()
    data = deepcopy(parent["input"])
    for employee in data["operational_day"]["employees"]:
        employee["qualifications"] = []
    revised = revise(client, parent, data, "remove-quals").json()
    optimized = client.post("/api/v1/optimizations", json=revised["input"])
    assert optimized.status_code == 202
    result_body = completed_result(client, optimized)
    assert "PUSH_QUALIFICATION_NOT_MET" in str(result_body)
    assert "CLOSE_QUALIFICATION_NOT_MET" in str(result_body)
    data["operational_day"]["employees"][0]["enabled"] = False
    result = revise(client, revised, data, "shortage")
    assert result.status_code == 201
    assert "INSUFFICIENT_ELIGIBLE_STAFF" in result.text


def test_missing_ids_wrong_hash_and_body_limit(context):
    from ramp_optimizer_imports.safety import UploadLimits

    client, sessions = context
    unknown = "00000000-0000-0000-0000-000000000099"
    assert client.get(f"/api/v1/operational-day-versions/{unknown}").status_code == 404
    parent = draft(client)
    parent["content_hash"] = "0" * 64
    response = revise(client, parent, _payload())
    assert response.status_code == 409 and response.json()["error"]["code"] == "STALE_INPUT_VERSION"
    with TestClient(
        create_app(session_factory=sessions, import_limits=UploadLimits(max_bytes=1024))
    ) as bounded:
        response = bounded.post(
            f"/api/v1/operational-days/{parent['operational_date']}/drafts",
            content=b" " * (257 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
