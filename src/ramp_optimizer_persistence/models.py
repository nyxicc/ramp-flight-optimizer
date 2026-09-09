"""Relational storage models kept outside the Phase 1 optimizer package."""

from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OperationalDayRow(Base):
    __tablename__ = "operational_days"
    __table_args__ = (
        Index("ix_operational_days_operational_date", "operational_date"),
        Index("ix_operational_days_created_at_utc", "created_at_utc"),
        Index("ix_operational_days_input_hash", "input_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operational_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at_utc: Mapped[str] = mapped_column(String(32), nullable=False)
    input_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    optimizer_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False)
    shift_count: Mapped[int] = mapped_column(Integer, nullable=False)
    flight_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fixed_assignment_count: Mapped[int] = mapped_column(Integer, nullable=False)

    employees: Mapped[list["EmployeeRow"]] = relationship(
        cascade="all, delete-orphan",
        order_by="EmployeeRow.ordinal",
        back_populates="operational_day",
    )
    shifts: Mapped[list["ShiftRow"]] = relationship(
        cascade="all, delete-orphan",
        order_by="ShiftRow.ordinal",
        back_populates="operational_day",
    )
    flights: Mapped[list["FlightRow"]] = relationship(
        cascade="all, delete-orphan",
        order_by="FlightRow.ordinal",
        back_populates="operational_day",
    )
    fixed_assignments: Mapped[list["FixedAssignmentRow"]] = relationship(
        cascade="all, delete-orphan",
        order_by="FixedAssignmentRow.ordinal",
        back_populates="operational_day",
    )
    optimization_runs: Mapped[list["OptimizationRunRow"]] = relationship(
        cascade="all, delete-orphan",
        back_populates="operational_day",
    )


class EmployeeRow(Base):
    __tablename__ = "operational_day_employees"
    __table_args__ = (
        UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_employees_ordinal",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operational_day_id: Mapped[str] = mapped_column(
        ForeignKey("operational_days.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qualifications_json: Mapped[str] = mapped_column(Text, nullable=False)

    operational_day: Mapped[OperationalDayRow] = relationship(back_populates="employees")


class ShiftRow(Base):
    __tablename__ = "operational_day_shifts"
    __table_args__ = (
        UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_shifts_ordinal",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operational_day_id: Mapped[str] = mapped_column(
        ForeignKey("operational_days.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(255), nullable=False)
    start_iso: Mapped[str] = mapped_column(String(64), nullable=False)
    end_iso: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_role: Mapped[str] = mapped_column(String(64), nullable=False)

    operational_day: Mapped[OperationalDayRow] = relationship(back_populates="shifts")


class FlightRow(Base):
    __tablename__ = "operational_day_flights"
    __table_args__ = (
        UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_flights_ordinal",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operational_day_id: Mapped[str] = mapped_column(
        ForeignKey("operational_days.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    arrival_flight_number: Mapped[str | None] = mapped_column(String(255))
    arrival_time_iso: Mapped[str | None] = mapped_column(String(64))
    departure_flight_number: Mapped[str | None] = mapped_column(String(255))
    departure_time_iso: Mapped[str | None] = mapped_column(String(64))
    gate: Mapped[str | None] = mapped_column(String(255))
    heavy: Mapped[bool] = mapped_column(Boolean, nullable=False)

    operational_day: Mapped[OperationalDayRow] = relationship(back_populates="flights")
    fixed_assignments: Mapped[list["FixedAssignmentRow"]] = relationship(
        back_populates="flight"
    )


class FixedAssignmentRow(Base):
    __tablename__ = "operational_day_fixed_assignments"
    __table_args__ = (
        UniqueConstraint(
            "operational_day_id",
            "ordinal",
            name="uq_operational_day_fixed_assignments_ordinal",
        ),
        UniqueConstraint(
            "operational_day_id",
            "employee_id",
            "flight_id",
            name="uq_operational_day_fixed_assignment",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operational_day_id: Mapped[str] = mapped_column(
        ForeignKey("operational_days.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(255), nullable=False)
    flight_id: Mapped[int] = mapped_column(
        ForeignKey("operational_day_flights.id", ondelete="RESTRICT"), nullable=False
    )

    operational_day: Mapped[OperationalDayRow] = relationship(
        back_populates="fixed_assignments"
    )
    flight: Mapped[FlightRow] = relationship(back_populates="fixed_assignments")


class OptimizationRunRow(Base):
    __tablename__ = "optimization_runs"
    __table_args__ = (
        Index("ix_optimization_runs_operational_day_id", "operational_day_id"),
        Index("ix_optimization_runs_created_at_utc", "created_at_utc"),
        Index("ix_optimization_runs_solver_status", "solver_status"),
        Index("ix_optimization_runs_operational_readiness", "operational_readiness"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operational_day_id: Mapped[str] = mapped_column(
        ForeignKey("operational_days.id", ondelete="CASCADE"), nullable=False
    )
    created_at_utc: Mapped[str] = mapped_column(String(32), nullable=False)
    package_version: Mapped[str] = mapped_column(String(64), nullable=False)
    api_version: Mapped[str] = mapped_column(String(32), nullable=False)
    result_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    solver_status: Mapped[str] = mapped_column(String(64), nullable=False)
    operational_readiness: Mapped[str] = mapped_column(String(64), nullable=False)
    emergency_pass_disposition: Mapped[str] = mapped_column(String(96), nullable=False)
    solver_runtime_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    objective_stage_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)

    operational_day: Mapped[OperationalDayRow] = relationship(
        back_populates="optimization_runs"
    )
