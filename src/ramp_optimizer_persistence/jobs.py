"""Durable queue transitions; compare-and-set claims never requeue running work."""

import json
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ramp_optimizer_persistence.errors import ResourceNotFoundError
from ramp_optimizer_persistence.models import OptimizationJobRow

TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"})


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_job(session: Session, job_id: str) -> OptimizationJobRow:
    row = session.get(OptimizationJobRow, job_id)
    if row is None:
        raise ResourceNotFoundError("Optimization job not found")
    return row


def claim_job(session: Session) -> OptimizationJobRow | None:
    job_id = session.scalar(
        select(OptimizationJobRow.id)
        .where(OptimizationJobRow.status == "QUEUED")
        .order_by(OptimizationJobRow.created_at, OptimizationJobRow.id)
        .limit(1)
    )
    if job_id is None:
        return None
    changed = session.execute(
        update(OptimizationJobRow)
        .where(OptimizationJobRow.id == job_id, OptimizationJobRow.status == "QUEUED")
        .values(
            status="RUNNING",
            started_at=now(),
            worker_token=str(uuid4()),
            progress_json=json.dumps({"phase": "STARTING"}),
        )
    )
    if cast(CursorResult, changed).rowcount != 1:
        return None
    return get_job(session, job_id)


def cancel_job(session: Session, job_id: str) -> None:
    get_job(session, job_id)
    session.execute(
        update(OptimizationJobRow)
        .where(OptimizationJobRow.id == job_id, OptimizationJobRow.status == "QUEUED")
        .values(
            status="CANCELLED",
            finished_at=now(),
            active_key=None,
            progress_json=json.dumps({"phase": "CANCELLED"}),
        )
    )
    session.execute(
        update(OptimizationJobRow)
        .where(OptimizationJobRow.id == job_id, OptimizationJobRow.status == "RUNNING")
        .values(status="CANCELLING")
    )
    session.expire_all()


def owned_job(session: Session, job_id: str, token: str) -> OptimizationJobRow | None:
    # Acquire the write transaction before inspecting cancellation to serialize
    # cancellation, checkpoint writes, and terminal publication on SQLite.
    changed = session.execute(
        update(OptimizationJobRow)
        .where(
            OptimizationJobRow.id == job_id,
            OptimizationJobRow.worker_token == token,
            OptimizationJobRow.status.in_(("RUNNING", "CANCELLING")),
        )
        .values(worker_token=token)
    )
    if cast(CursorResult, changed).rowcount != 1:
        return None
    session.expire_all()
    return get_job(session, job_id)
