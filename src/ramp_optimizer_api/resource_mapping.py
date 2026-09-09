"""Explicit mapping from persistence records to public resource schemas."""

from ramp_optimizer_persistence.repositories import (
    OperationalDayRecord,
    OperationalDaySummaryRecord,
    OptimizationRunRecord,
    OptimizationRunSummaryRecord,
)
from ramp_optimizer_api.schemas import (
    OperationalDayResourceResponse,
    OperationalDaySummaryResponse,
    OptimizationRunResourceResponse,
    OptimizationRunSummaryResponse,
    OptimizationResponse,
)
from ramp_optimizer_api.mapping import operational_day_to_request


def operational_day_summary_response(
    value: OperationalDaySummaryRecord,
) -> OperationalDaySummaryResponse:
    return OperationalDaySummaryResponse(
        id=value.id,
        operational_date=value.operational_date,
        created_at_utc=value.created_at_utc,
        input_schema_version=value.input_schema_version,
        input_hash=value.input_hash,
        employee_count=value.employee_count,
        shift_count=value.shift_count,
        flight_count=value.flight_count,
        fixed_assignment_count=value.fixed_assignment_count,
    )


def operational_day_resource_response(
    value: OperationalDayRecord,
) -> OperationalDayResourceResponse:
    summary = operational_day_summary_response(value)
    return OperationalDayResourceResponse(
        **summary.model_dump(),
        input=operational_day_to_request(value.day, value.config),
    )


def optimization_run_summary_response(
    value: OptimizationRunSummaryRecord,
) -> OptimizationRunSummaryResponse:
    return OptimizationRunSummaryResponse(
        id=value.id,
        operational_day_id=value.operational_day_id,
        created_at_utc=value.created_at_utc,
        package_version=value.package_version,
        api_version=value.api_version,
        result_schema_version=value.result_schema_version,
        input_hash=value.input_hash,
        solver_status=value.solver_status,
        operational_readiness=value.operational_readiness,
        emergency_pass_disposition=value.emergency_pass_disposition,
        solver_runtime_seconds=value.solver_runtime_seconds,
        attempt_count=value.attempt_count,
        objective_stage_count=value.objective_stage_count,
        warning_count=value.warning_count,
    )


def optimization_run_resource_response(
    value: OptimizationRunRecord,
) -> OptimizationRunResourceResponse:
    summary = optimization_run_summary_response(value)
    return OptimizationRunResourceResponse(
        **summary.model_dump(),
        result=OptimizationResponse.model_validate(value.result),
    )
