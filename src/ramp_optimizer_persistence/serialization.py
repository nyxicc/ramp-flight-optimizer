"""Canonical, versioned JSON serialization for persisted public contracts."""

from hashlib import sha256
import json

from dataclasses import fields

from ramp_optimizer import OperationalDay, OptimizerConfig


INPUT_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1


def canonical_input_json(day: OperationalDay, config: OptimizerConfig) -> str:
    return _canonical_json(resolved_input_payload(day, config))


def canonical_input_hash(day: OperationalDay, config: OptimizerConfig) -> str:
    return sha256(canonical_input_json(day, config).encode("utf-8")).hexdigest()


def config_snapshot_json(config: OptimizerConfig) -> str:
    return _canonical_json(_config_payload(config))


def optimization_result_json(result: dict[str, object]) -> str:
    return _canonical_json(result)


def resolved_input_payload(day: OperationalDay, config: OptimizerConfig) -> dict[str, object]:
    """Return the complete, versioned input without depending on the HTTP adapter."""

    return {
        "operational_day": {
            "operational_date": day.operational_date.isoformat(),
            "employees": [
                {
                    "employee_id": item.employee_id,
                    "name": item.name,
                    "qualifications": sorted(value.value for value in item.qualifications),
                    "enabled": item.enabled,
                }
                for item in day.employees
            ],
            "employee_shifts": [
                {
                    "employee_id": item.employee_id,
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "normalized_role": item.normalized_role.value,
                }
                for item in day.employee_shifts
            ],
            "flights": [
                {
                    "arrival_flight_number": item.arrival_flight_number,
                    "arrival_time": item.arrival_time.isoformat() if item.arrival_time else None,
                    "departure_flight_number": item.departure_flight_number,
                    "departure_time": item.departure_time.isoformat() if item.departure_time else None,
                    "gate": item.gate,
                    "heavy": item.heavy,
                }
                for item in day.flights
            ],
            "fixed_assignments": [
                {
                    "employee_id": item.employee_id,
                    "flight": {
                        "arrival_flight_number": item.flight.arrival_flight_number,
                        "departure_flight_number": item.flight.departure_flight_number,
                    },
                }
                for item in day.fixed_assignments
            ],
        },
        "config": _config_payload(config),
    }


def _config_payload(config: OptimizerConfig) -> dict[str, object]:
    return {item.name: getattr(config, item.name) for item in fields(config)}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
