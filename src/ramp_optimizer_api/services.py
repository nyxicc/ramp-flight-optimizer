"""Application services coordinating short transactions and synchronous solves."""

from importlib.metadata import version as distribution_version
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError

from ramp_optimizer import optimize_flight_assignments, validate_or_raise
from ramp_optimizer_api.errors import FixedAssignmentReferenceError
from ramp_optimizer_api.mapping import map_optimization_request, optimization_result_to_response
from ramp_optimizer_api.policy import enforce_synchronous_policy
from ramp_optimizer_api.schemas import OptimizationRequest
from ramp_optimizer_persistence.errors import PersistenceError
from ramp_optimizer_persistence.repositories import (
    Clock,
    IdProvider,
    OperationalDayRecord,
    OperationalDaySummaryRecord,
    OptimizationRunRecord,
    OptimizationRunSummaryRecord,
    create_operational_day,
    create_optimization_run,
    get_operational_day,
    get_optimization_run,
    list_operational_days,
    list_optimization_runs_for_day,
)
from ramp_optimizer_persistence.database import SessionFactory
from ramp_optimizer_persistence.errors import DatabaseOperationError
from ramp_optimizer_persistence.imports import is_imported_snapshot
from ramp_optimizer_imports.models import ImportError


PACKAGE_DISTRIBUTION = "ramp-flight-optimizer"


class PersistenceService:
    """Coordinates repositories without holding a transaction during CP-SAT."""

    @property
    def session_factory(self):
        """Share the configured transaction boundary with other application services."""
        return self._session_factory

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        api_version: str,
        id_provider: IdProvider | None = None,
        clock: Clock | None = None,
        optimizer: Callable = optimize_flight_assignments,
        package_version_provider: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._api_version = api_version
        self._id_provider = id_provider
        self._clock = clock
        self._optimizer = optimizer
        self._package_version_provider = package_version_provider or (
            lambda: distribution_version(PACKAGE_DISTRIBUTION)
        )

    def create_operational_day(self, request: OptimizationRequest) -> OperationalDayRecord:
        mapped = map_optimization_request(request)
        if mapped.issues:
            raise FixedAssignmentReferenceError(mapped.issues)
        validate_or_raise(mapped.operational_day, mapped.config)
        try:
            with self._session_factory() as session:
                with session.begin():
                    return create_operational_day(
                        session,
                        mapped.operational_day,
                        mapped.config,
                        id_provider=self._id_provider,
                        clock=self._clock,
                    )
        except (PersistenceError, FixedAssignmentReferenceError):
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("Database operation failed.") from error

    def get_operational_day(self, resource_id: str) -> OperationalDayRecord:
        try:
            with self._session_factory() as session:
                return get_operational_day(session, resource_id)
        except PersistenceError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("Database operation failed.") from error

    def list_operational_days(
        self,
        *,
        limit: int,
        offset: int,
        operational_date=None,
    ) -> tuple[tuple[OperationalDaySummaryRecord, ...], int]:
        try:
            with self._session_factory() as session:
                return list_operational_days(
                    session,
                    limit=limit,
                    offset=offset,
                    operational_date=operational_date,
                )
        except SQLAlchemyError as error:
            raise DatabaseOperationError("Database operation failed.") from error

    def optimize_operational_day(self, resource_id: str) -> OptimizationRunRecord:
        snapshot = self.get_operational_day(resource_id)
        if not snapshot.day.flights:
            try:
                with self._session_factory() as session:
                    imported = is_imported_snapshot(session, resource_id)
            except SQLAlchemyError:
                raise DatabaseOperationError("Database operation failed.") from None
            if imported:
                raise ImportError("FLIGHT_DATA_REQUIRED", 409)
        validate_or_raise(snapshot.day, snapshot.config)
        enforce_synchronous_policy(snapshot.config.solver_time_limit_seconds)
        result = self._optimizer(snapshot.day, snapshot.config)
        response = optimization_result_to_response(result)
        try:
            with self._session_factory() as session:
                with session.begin():
                    return create_optimization_run(
                        session,
                        snapshot,
                        response.model_dump(mode="json"),
                        package_version=self._package_version_provider(),
                        api_version=self._api_version,
                        id_provider=self._id_provider,
                        clock=self._clock,
                    )
        except PersistenceError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("Database operation failed.") from error

    def get_optimization_run(self, resource_id: str) -> OptimizationRunRecord:
        try:
            with self._session_factory() as session:
                return get_optimization_run(session, resource_id)
        except PersistenceError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("Database operation failed.") from error

    def list_optimization_runs(
        self,
        operational_day_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[OptimizationRunSummaryRecord, ...], int]:
        self.get_operational_day(operational_day_id)
        try:
            with self._session_factory() as session:
                return list_optimization_runs_for_day(
                    session,
                    operational_day_id,
                    limit=limit,
                    offset=offset,
                )
        except PersistenceError:
            raise
        except SQLAlchemyError as error:
            raise DatabaseOperationError("Database operation failed.") from error
