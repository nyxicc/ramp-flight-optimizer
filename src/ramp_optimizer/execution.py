"""Optional process-local observation; no scheduling policy or global worker state."""

from contextvars import ContextVar
from typing import Callable

from ramp_optimizer.models import OptimizationResult

observer: ContextVar[Callable[[dict, OptimizationResult | None], None] | None] = ContextVar(
    "optimizer_observer", default=None
)
