"""FastAPI application boundary for synchronous version 1 optimization."""

from importlib.metadata import version as distribution_version
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, Query, Request, status
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
from ramp_optimizer_api.policy import enforce_synchronous_policy
from ramp_optimizer_api.resource_mapping import (
    operational_day_resource_response,
    operational_day_summary_response,
    optimization_run_resource_response,
    optimization_run_summary_response,
)
from ramp_optimizer_api.schemas import (
    ErrorBody,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    OptimizationRequest,
    OptimizationResponse,
    OperationalDayListResponse,
    OperationalDayResourceResponse,
    OptimizationRunListResponse,
    OptimizationRunResourceResponse,
    ValidationResponse,
    VersionResponse,
)
from ramp_optimizer_api.services import PersistenceService
from ramp_optimizer_persistence.database import (
    SessionFactory,
    create_database_engine,
    make_session_factory,
)
from ramp_optimizer_persistence.errors import (
    DatabaseOperationError,
    PersistenceConflictError,
    PersistenceIntegrityError,
    ResourceNotFoundError,
)
from ramp_optimizer_persistence.settings import DatabaseSettings
from ramp_optimizer_api.import_routes import BoundedImportBody, import_router
from ramp_optimizer_imports.models import ImportError
from ramp_optimizer_imports.safety import UploadLimits
from ramp_optimizer_imports.services import ImportService
from ramp_optimizer_persistence.imports import import_transactions


API_VERSION = "1"
API_PREFIX = "/api/v1"
PACKAGE_DISTRIBUTION = "ramp-flight-optimizer"
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


def create_app(
    *,
    session_factory: SessionFactory | None = None,
    persistence_service: PersistenceService | None = None,
    import_service: ImportService | None = None,
    import_limits: UploadLimits | None = None,
) -> FastAPI:
    """Build an independent application with no request-global mutable state."""

    application = FastAPI(
        title="Ramp Flight Optimizer API",
        version=API_VERSION,
        description=(
            "Versioned synchronous API adapter for the Phase 1 ramp optimizer."
        ),
    )
    if persistence_service is None:
        active_session_factory = session_factory
        if active_session_factory is None:
            settings = DatabaseSettings.from_environment()
            active_session_factory = make_session_factory(
                create_database_engine(settings.database_url)
            )
        persistence_service = PersistenceService(
            active_session_factory,
            api_version=API_VERSION,
            optimizer=optimize_flight_assignments,
        )
    _register_exception_handlers(application)
    limits = import_limits or (import_service.limits if import_service else UploadLimits.from_environment())
    if import_service is None:
        import_service = ImportService(import_transactions(persistence_service.session_factory), limits=limits)
    application.add_middleware(BoundedImportBody, max_bytes=limits.max_bytes)
    application.include_router(import_router(import_service))
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
        enforce_synchronous_policy(mapped.config.solver_time_limit_seconds)
        result = optimize_flight_assignments(mapped.operational_day, mapped.config)
        return optimization_result_to_response(result)

    @router.post(
        "/operational-days",
        response_model=OperationalDayResourceResponse,
        status_code=status.HTTP_201_CREATED,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
        tags=["stored operational days"],
    )
    def create_stored_day(request: OptimizationRequest) -> OperationalDayResourceResponse:
        return operational_day_resource_response(
            persistence_service.create_operational_day(request)
        )

    @router.get(
        "/operational-days",
        response_model=OperationalDayListResponse,
        tags=["stored operational days"],
    )
    def list_stored_days(
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
        operational_date: date | None = None,
    ) -> OperationalDayListResponse:
        records, total = persistence_service.list_operational_days(
            limit=limit,
            offset=offset,
            operational_date=operational_date,
        )
        return OperationalDayListResponse(
            items=tuple(operational_day_summary_response(item) for item in records),
            total=total,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/operational-days/{operational_day_id}",
        response_model=OperationalDayResourceResponse,
        tags=["stored operational days"],
    )
    def get_stored_day(operational_day_id: UUID) -> OperationalDayResourceResponse:
        return operational_day_resource_response(
            persistence_service.get_operational_day(str(operational_day_id))
        )

    @router.post(
        "/operational-days/{operational_day_id}/optimizations",
        response_model=OptimizationRunResourceResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["stored optimization runs"],
    )
    def optimize_stored_day(
        operational_day_id: UUID,
    ) -> OptimizationRunResourceResponse:
        return optimization_run_resource_response(
            persistence_service.optimize_operational_day(str(operational_day_id))
        )

    @router.get(
        "/operational-days/{operational_day_id}/optimization-runs",
        response_model=OptimizationRunListResponse,
        tags=["stored optimization runs"],
    )
    def list_stored_runs(
        operational_day_id: UUID,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> OptimizationRunListResponse:
        records, total = persistence_service.list_optimization_runs(
            str(operational_day_id),
            limit=limit,
            offset=offset,
        )
        return OptimizationRunListResponse(
            items=tuple(optimization_run_summary_response(item) for item in records),
            total=total,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/optimization-runs/{optimization_run_id}",
        response_model=OptimizationRunResourceResponse,
        tags=["stored optimization runs"],
    )
    def get_stored_run(
        optimization_run_id: UUID,
    ) -> OptimizationRunResourceResponse:
        return optimization_run_resource_response(
            persistence_service.get_optimization_run(str(optimization_run_id))
        )

    application.include_router(router)
    return application


def _register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(ImportError)
    async def import_error_handler(_request: Request, error: ImportError) -> JSONResponse:
        return _error_response(error.status_code, code=error.code,
            message="The import request requires review or correction.",
            details=tuple(ErrorDetail(code=i.code, path=i.field or "import", message=i.message) for i in error.issues))

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

    @application.exception_handler(ResourceNotFoundError)
    async def resource_not_found_handler(
        _request: Request,
        _error: ResourceNotFoundError,
    ) -> JSONResponse:
        return _error_response(
            404,
            code="RESOURCE_NOT_FOUND",
            message="The requested resource was not found.",
        )

    @application.exception_handler(PersistenceConflictError)
    async def persistence_conflict_handler(
        _request: Request,
        _error: PersistenceConflictError,
    ) -> JSONResponse:
        return _error_response(
            409,
            code="PERSISTENCE_CONFLICT",
            message="The resource conflicts with existing stored data.",
        )

    @application.exception_handler(PersistenceIntegrityError)
    async def persistence_integrity_handler(
        _request: Request,
        _error: PersistenceIntegrityError,
    ) -> JSONResponse:
        return _error_response(
            500,
            code="PERSISTENCE_INTEGRITY_ERROR",
            message="Stored resource integrity verification failed.",
        )

    @application.exception_handler(DatabaseOperationError)
    async def database_operation_handler(
        _request: Request,
        _error: DatabaseOperationError,
    ) -> JSONResponse:
        return _error_response(
            500,
            code="DATABASE_OPERATION_FAILED",
            message="The database operation could not be completed.",
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
