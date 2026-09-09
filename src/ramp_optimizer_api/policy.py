"""HTTP execution policies kept separate from domain validation."""

from ramp_optimizer import ValidationIssue
from ramp_optimizer_api.errors import SynchronousPolicyError


MAX_SYNCHRONOUS_SOLVER_TIME_SECONDS = 60.0


def enforce_synchronous_policy(solver_time_limit_seconds: float) -> None:
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
