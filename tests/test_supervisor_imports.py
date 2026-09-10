# ruff: noqa: F811
"""Single-day flight imports and workbook-owned ramp employee review."""

import json
from datetime import date
from io import BytesIO
from uuid import uuid4

from openpyxl import load_workbook

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer_imports.daily_flight_log import DailyFlightLogAdapter
from ramp_optimizer_imports.serialization import canonical_json, load_preview
from tests.import_fixtures import DAY, SECRET, row, workbook
from tests.test_flight_import import POLICY
from tests.test_flight_import import workbook as flight_workbook
from tests.test_import_workflow import client, storage, upload  # noqa: F401


def test_workbook_names_and_only_occupied_ramp_agents_are_imported(client):
    content = workbook(
        [
            row(Employee="Jordan Example"),
            row(Employee="Lead Not Included", Position="Ramp Lead"),
            row(Employee="Counter Not Included", Position="Customer Service", Start=None),
            row(Employee=None),
        ]
    )
    response = upload(
        client, content, metadata={"operational_date": str(DAY), "ramp_agents_only": True}
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert len(record["preview"]["rows"]) == 1
    reviewed = record["preview"]["rows"][0]
    assert reviewed["employee_name"] == "Jordan Example"
    assert reviewed["match_status"] == "MATCHED"
    assert record["preview"]["confirmation_eligible"]
    employee = record["preview"]["employees"][0]
    assert employee["name"] == "Jordan Example"
    assert employee["qualifications"] == []
    assert reviewed["employee_id"] == employee["employee_id"]
    assert SECRET not in response.text
    assert "Not Included" not in response.text
    confirmed = client.post(f"/api/v1/imports/{record['import_id']}/confirm", json={"revision": 1})
    assert confirmed.status_code == 200
    snapshot = client.get(
        f"/api/v1/operational-days/{confirmed.json()['confirmed_operational_day_id']}"
    ).json()
    assert len(snapshot["input"]["operational_day"]["employees"]) == 1
    assert snapshot["input"]["operational_day"]["employees"][0]["name"] == "Jordan Example"


def test_approved_qualifications_are_preserved_and_workbook_ids_are_stable(client):
    metadata = {
        "operational_date": str(DAY),
        "ramp_agents_only": True,
        "roster": [
            {"employee_id": "approved", "name": "Jordan Example", "qualifications": ["PUSH"]}
        ],
    }
    content = workbook([row(Employee="Jordan Example"), row(Employee="Taylor Example")])
    first = upload(client, content, metadata=metadata).json()["preview"]
    second = upload(client, content, metadata=metadata).json()["preview"]
    assert first["employees"] == second["employees"]
    assert first["employees"][0]["employee_id"] == "approved"
    assert first["employees"][0]["qualifications"] == ["PUSH"]
    assert first["employees"][1]["qualifications"] == []


def test_ramp_only_rejects_no_ramp_agents(client):
    record = upload(
        client,
        workbook([row(Position="Ramp Lead")]),
        metadata={"operational_date": str(DAY), "ramp_agents_only": True},
    ).json()
    assert not record["preview"]["confirmation_eligible"]
    assert any(i["code"] == "RAMP_AGENTS_REQUIRED" for i in record["preview"]["issues"])


def test_workbook_title_dates_are_not_an_additional_schedule_date():
    book = load_workbook(BytesIO(flight_workbook()))
    book.active["A1"] = "Flight log 01/01/1999 - template updated 99/99/9999"
    stream = BytesIO()
    book.save(stream)
    book.close()
    selected_day = date(2035, 4, 15)
    preview = DailyFlightLogAdapter().parse(
        stream.getvalue(), str(uuid4()), selected_day, POLICY, OptimizerConfig()
    )
    assert preview.confirmation_eligible
    assert preview.flight_rows[0].flight.arrival_time.date() == selected_day
    assert preview.detected_date is None
    assert load_preview(canonical_json(preview)) == preview


def test_flight_review_all_accepts_one_revision_and_preserves_times(client):
    content = flight_workbook([[101, "ABC", "10:00"], [103, "DEF", "12:00"]])
    response = client.post(
        "/api/v1/imports/daily-flight-log",
        files={"workbook": ("log.xlsx", content)},
        data={
            "metadata": json.dumps(
                {
                    "operational_date": str(DAY),
                    "time_policy": {
                        "airport_timezone": "America/Chicago",
                        "operational_day_start": "00:00",
                        "planning_basis": "SCHEDULED",
                    },
                }
            )
        },
    )
    assert response.status_code == 201, response.text
    record = response.json()
    changes = [{"row_id": r["row_id"], "heavy": False} for r in record["preview"]["flight_rows"]]
    corrected = client.post(
        f"/api/v1/imports/{record['import_id']}/corrections",
        json={"revision": 1, "flight_corrections": changes},
    )
    assert corrected.status_code == 200, corrected.text
    preview = corrected.json()["preview"]
    assert preview["revision"] == 2
    assert all(r["heavy_reviewed"] for r in preview["flight_rows"])
    assert not preview["issues"]
    assert [r["flight"] for r in preview["flight_rows"]] == [
        r["flight"] for r in record["preview"]["flight_rows"]
    ]
