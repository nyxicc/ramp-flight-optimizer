"""FastAPI application boundary for synchronous version 1 optimization."""

from importlib.metadata import version as distribution_version
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ramp_optimizer import (
    InputValidationError,
    ValidationIssue,
    optimize_flight_assignments,
    validate_config,
    validate_operational_day,
    validate_or_raise,
)
from ramp_optimizer_api.errors import (
    FixedAssignmentReferenceError,
    SynchronousPolicyError,
)
from ramp_optimizer_api.mapping import (
    map_optimization_request,
    optimization_result_to_response,
    validation_issue_to_response,
)
from ramp_optimizer_api.schemas import (
    ErrorBody,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    OptimizationRequest,
    OptimizationResponse,
    ValidationResponse,
    VersionResponse,
)


API_VERSION = "1"
API_PREFIX = "/api/v1"
PACKAGE_DISTRIBUTION = "ramp-flight-optimizer"
MAX_SYNCHRONOUS_SOLVER_TIME_SECONDS = 60.0


def create_app() -> FastAPI:
    """Build an independent application with no request-global mutable state."""

    application = FastAPI(
        title="Ramp Flight Optimizer API",
        version=API_VERSION,
        description=(
            "Versioned synchronous API adapter for the Phase 1 ramp optimizer."
        ),
    )
    _register_exception_handlers(application)
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @router.get("/version", response_model=VersionResponse, tags=["system"])
    def package_version() -> VersionResponse:
        return VersionResponse(
            api_version=API_VERSION,
            package_version=distribution_version(PACKAGE_DISTRIBUTION),
        )

    @router.post(
        "/operational-days/validate",
        response_model=ValidationResponse,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
        tags=["operational days"],
    )
    def validate_day(request: OptimizationRequest) -> ValidationResponse:
        mapped = map_optimization_request(request)
        issues = (
            mapped.issues
            + validate_config(mapped.config)
            + validate_operational_day(mapped.operational_day, mapped.config)
        )
        return ValidationResponse(
            valid=not issues,
            issues=tuple(validation_issue_to_response(item) for item in issues),
        )

    @router.post(
        "/optimizations",
        response_model=OptimizationResponse,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
        tags=["optimizations"],
    )
    def optimize(request: OptimizationRequest) -> OptimizationResponse:
        mapped = map_optimization_request(request)
        if mapped.issues:
            raise FixedAssignmentReferenceError(mapped.issues)
        validate_or_raise(mapped.operational_day, mapped.config)
        _enforce_synchronous_policy(mapped.config.solver_time_limit_seconds)
        result = optimize_flight_assignments(mapped.operational_day, mapped.config)
        return optimization_result_to_response(result)

    application.include_router(router)
    return application


def _enforce_synchronous_policy(solver_time_limit_seconds: float) -> None:
    if solver_time_limit_seconds > MAX_SYNCHRONOUS_SOLVER_TIME_SECONDS:
        raise SynchronousPolicyError(
            ValidationIssue(
                "SYNCHRONOUS_TIME_LIMIT_EXCEEDED",
                "config.solver_time_limit_seconds",
                (
                    f"must not exceed {MAX_SYNCHRONOUS_SOLVER_TIME_SECONDS:g} "
                    "seconds for the synchronous API"
                ),
            )
        )


def _register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        details = tuple(
            ErrorDetail(
                code=str(item.get("type", "value_error")).upper(),
                path=_format_location(item.get("loc", ())),
                message=str(item.get("msg", "Invalid request value.")),
            )
            for item in error.errors()
        )
        return _error_response(
            422,
            code="REQUEST_VALIDATION_ERROR",
            message="Request data did not match the API schema.",
            details=details,
        )

    @application.exception_handler(FixedAssignmentReferenceError)
    async def fixed_reference_handler(
        _request: Request,
        error: FixedAssignmentReferenceError,
    ) -> JSONResponse:
        return _error_response(
            422,
            code="FIXED_ASSIGNMENT_REFERENCE_INVALID",
            message="A fixed-assignment flight reference did not resolve exactly once.",
            details=tuple(_issue_detail(item) for item in error.issues),
        )

    @application.exception_handler(InputValidationError)
    async def domain_validation_handler(
        _request: Request,
        error: InputValidationError,
    ) -> JSONResponse:
        return _error_response(
            422,
            code="OPTIMIZER_INPUT_INVALID",
            message="Operational-day input failed validation.",
            details=tuple(_issue_detail(item) for item in error.issues),
        )

    @application.exception_handler(SynchronousPolicyError)
    async def synchronous_policy_handler(
        _request: Request,
        error: SynchronousPolicyError,
    ) -> JSONResponse:
        return _error_response(
            422,
            code="SYNCHRONOUS_POLICY_VIOLATION",
            message="Request exceeds the synchronous API safety policy.",
            details=(_issue_detail(error.issue),),
        )

    @application.exception_handler(Exception)
    async def internal_error_handler(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return _error_response(
            500,
            code="INTERNAL_SERVER_ERROR",
            message="The optimizer request could not be completed.",
        )


def _issue_detail(issue: ValidationIssue) -> ErrorDetail:
    return ErrorDetail(code=issue.code, path=issue.path, message=issue.message)


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    details: tuple[ErrorDetail, ...] = (),
) -> JSONResponse:
    envelope = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details)
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def _format_location(location: Any) -> str:
    parts = tuple(location) if isinstance(location, (tuple, list)) else (location,)
    rendered = ""
    for part in parts:
        if part == "body" and not rendered:
            continue
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = str(part)
    return rendered or "request"


app = create_app()
