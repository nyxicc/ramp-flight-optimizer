"""Closed import vocabulary and explicit terminal states."""

from enum import StrEnum


class ImportType(StrEnum):
    TEAMWORK_EMPLOYEE_SCHEDULE = "TEAMWORK_EMPLOYEE_SCHEDULE"


class ImportStatus(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    READY_TO_CONFIRM = "READY_TO_CONFIRM"
    REJECTED = "REJECTED"
    CONFIRMED = "CONFIRMED"


TRANSITIONS = {
    ImportStatus.REVIEW_REQUIRED: frozenset({ImportStatus.REVIEW_REQUIRED, ImportStatus.READY_TO_CONFIRM}),
    ImportStatus.READY_TO_CONFIRM: frozenset({ImportStatus.REVIEW_REQUIRED, ImportStatus.READY_TO_CONFIRM, ImportStatus.CONFIRMED}),
    ImportStatus.REJECTED: frozenset(),
    ImportStatus.CONFIRMED: frozenset(),
}
