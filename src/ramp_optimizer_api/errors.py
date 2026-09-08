"""API-boundary exceptions that do not belong to the optimizer domain."""

from ramp_optimizer import ValidationIssue


class FixedAssignmentReferenceError(ValueError):
    """Raised when an API flight reference cannot resolve exactly once."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        self.issues = issues
        super().__init__("Fixed-assignment flight reference resolution failed.")


class SynchronousPolicyError(ValueError):
    """Raised when a valid request exceeds the synchronous API safety policy."""

    def __init__(self, issue: ValidationIssue) -> None:
        self.issue = issue
        super().__init__(issue.message)
