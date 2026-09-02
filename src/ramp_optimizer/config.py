"""Central configuration for operational and import assumptions."""

from dataclasses import dataclass

from ramp_optimizer.enums import OperationalRole


DEFAULT_POSITION_ROLE_MAPPINGS: tuple[tuple[str, OperationalRole], ...] = (
    ("Ramp Agent", OperationalRole.RAMP_AGENT),
    ("Ramp Lead", OperationalRole.RAMP_LEAD),
    ("Lead", OperationalRole.RAMP_LEAD),
    ("Ramp Trainee", OperationalRole.TRAINEE),
    ("New Hire Training", OperationalRole.TRAINEE),
    ("Ramp Instructor", OperationalRole.POSSIBLE_RAMP_SUPPORT),
    ("Customer Service Agent", OperationalRole.NON_RAMP),
    ("Cabin Cleaner", OperationalRole.NON_RAMP),
    ("Bagroom", OperationalRole.UNKNOWN),
    ("Airline-Specific Ramp", OperationalRole.UNKNOWN),
    ("Operations", OperationalRole.UNKNOWN),
)


@dataclass(frozen=True, slots=True)
class OptimizerConfig:
    """Configurable Phase 1 assumptions.

    Workload factors are synthetic, explainable defaults rather than empirically
    validated measurements. CP-SAT will later convert them to scaled integers.
    """

    arrival_preparation_minutes: int = 10
    arrival_offload_minutes: int = 20
    departure_work_minutes: int = 60

    minimum_staff: int = 3
    normal_preferred_staff: int = 4
    heavy_preferred_staff: int = 5

    required_break_minutes: int = 30
    consecutive_reset_minutes: int = 40
    continuity_horizon_minutes: int = 120

    express_threshold: int = 3000
    express_workload_factor: float = 0.80
    three_person_workload_multiplier: float = 1.15
    workload_scale: int = 100

    allow_leads_for_minimum_staffing: bool = False
    allow_trainees_for_assignments: bool = False
    allow_possible_ramp_support_for_assignments: bool = False

    solver_time_limit_seconds: float = 30.0
    solver_random_seed: int = 42
    solver_num_search_workers: int = 1


@dataclass(frozen=True, slots=True)
class TeamWorkImportConfig:
    """Rules for importing a TeamWork daily schedule export.

    Position mappings are explicit data. Mapping a label to ``UNKNOWN`` keeps the
    label in a review queue and never makes the row ramp-eligible.
    """

    worksheet_name: str = "Schedule"
    header_scan_limit: int = 50
    maximum_shift_hours: float = 18.0
    hours_tolerance: float = 0.10
    position_role_mappings: tuple[
        tuple[str, OperationalRole], ...
    ] = DEFAULT_POSITION_ROLE_MAPPINGS
    vacancy_position_placeholders: frozenset[str] = frozenset(
        {"empty", "(empty)", "open", "open shift", "vacant", "vacancy"}
    )
