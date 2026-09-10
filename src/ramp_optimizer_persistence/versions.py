"""Transaction-neutral append-only version repository."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ramp_optimizer_persistence.errors import ResourceNotFoundError
from ramp_optimizer_persistence.models import InputVersionRow


def get_version(session: Session, version_id: str) -> InputVersionRow:
    row = session.get(InputVersionRow, version_id)
    if row is None:
        raise ResourceNotFoundError("Input version not found")
    return row


def versions_for_date(session: Session, day: date):
    return (
        select(InputVersionRow)
        .where(InputVersionRow.operational_date == day)
        .order_by(InputVersionRow.version_number)
    )


def append_version(session: Session, row: InputVersionRow) -> None:
    session.add(row)
    session.flush()
