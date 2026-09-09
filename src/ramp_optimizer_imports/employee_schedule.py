"""TeamWork adapter and deterministic reviewed schedule validation."""

from dataclasses import replace
from datetime import date, datetime
from io import BytesIO
from uuid import UUID, uuid5

from ramp_optimizer.config import OptimizerConfig, TeamWorkImportConfig
from ramp_optimizer.enums import IssueSeverity, OperationalRole, Qualification
from ramp_optimizer.models import Employee, EmployeeShift, OperationalDay
from ramp_optimizer.teamwork_import import import_teamwork_schedule
from ramp_optimizer.validation import validate_config, validate_operational_day
from ramp_optimizer_imports.enums import ImportType
from ramp_optimizer_imports.models import ImportError, ImportPreview, ReviewIssue, ReviewRow, RowCorrection


class TeamWorkAdapter:
    import_type = ImportType.TEAMWORK_EMPLOYEE_SCHEDULE
    schema_version = 1

    def revalidate(self, preview):
        return revalidate(preview)

    def correct(self, preview, corrections):
        return apply_corrections(preview, corrections)

    def snapshot(self, preview):
        return to_day(preview)

    def parse(self, content: bytes, import_id: str, operational_date: date,
              roster: tuple[Employee, ...], config: OptimizerConfig) -> ImportPreview:
        settings = TeamWorkImportConfig()
        parsed = import_teamwork_schedule(BytesIO(content), roster, settings)
        source = tuple(ReviewIssue(
            i.code, i.severity, i.message, i.source_row, i.column,
            i.severity in {IssueSeverity.ERROR, IssueSeverity.FATAL}
            or i.code in {'UNMATCHED_EMPLOYEE', 'AMBIGUOUS_EMPLOYEE'},
        ) for i in parsed.issues)
        preview = ImportPreview(
            1, operational_date, roster, roster,
            tuple(ReviewRow(str(uuid5(UUID(import_id), f'schedule:{r.source_row}')), r)
                  for r in parsed.review_rows), source, (), config, settings,
        )
        return revalidate(preview)


def issue(code: str, row=None, field=None, *, warning=False) -> ReviewIssue:
    messages = {
        'UNMATCHED_EMPLOYEE': 'The active row must be linked to an authoritative roster employee.',
        'AMBIGUOUS_EMPLOYEE': 'Multiple roster employees match this row; select one explicitly.',
        'UNKNOWN_SHIFT_EMPLOYEE': 'The selected employee ID does not exist in the authoritative roster.',
        'FORMULA_VALUE_UNAVAILABLE': 'Replace the required formula value with an explicit reviewed value.',
        'MISSING_POSITION': 'The occupied row requires an explicitly reviewed operational role.',
        'UNKNOWN_POSITION': 'The role is unknown and does not grant ramp eligibility.',
        'INVALID_SHIFT_TIME': 'The shift requires valid start and end datetimes.',
        'INVALID_SHIFT_RANGE': 'Shift start must be earlier than shift end.',
        'INVALID_DATE': 'Shift start must fall on the selected operational date.',
        'MIXED_DATETIME_AWARENESS': 'Shift start and end must use consistent timezone awareness.',
        'IMPLAUSIBLY_LONG_SHIFT': 'The shift exceeds the configured maximum duration.',
        'HOURS_DURATION_MISMATCH': 'Imported Hours differs from the reviewed shift duration.',
        'UNKNOWN_IMPORT_ROW': 'The correction does not identify a row in this import.',
        'DUPLICATE_CORRECTION_TARGET': 'A row may be corrected only once per request.',
        'CONFLICTING_EMPLOYEE_OVERRIDE': 'Corrections assign conflicting values to the same employee property.',
        'EMPLOYEE_OVERRIDE_REQUIRES_ACTIVE_MATCH': 'Employee overrides require an active occupied row linked to the roster.',
        'VACANCY_RESTORATION_REQUIRED': 'Set vacancy to false explicitly when selecting an employee for a vacancy.',
        'INVALID_OPERATIONAL_ROLE': 'Choose an allowed operational role.',
        'INVALID_EMPLOYEE_OVERRIDE': 'Use a boolean enabled value and allowed qualification values.',
        'INVALID_CORRECTION_VALUE': 'Use valid datetimes and booleans for the corrected fields.',
    }
    return ReviewIssue(
        code, IssueSeverity.WARNING if warning else IssueSeverity.ERROR,
        messages.get(code, 'Review the indicated schedule value.'),
        row, field, not warning, 'Correct the value or explicitly exclude the source row.' if row else None,
    )


def to_day(preview: ImportPreview) -> OperationalDay:
    return OperationalDay(
        preview.operational_date, preview.employees,
        tuple(EmployeeShift(r.employee_id, r.start, r.end, r.normalized_role)
              for wrapped in preview.rows if not (r := wrapped.values).excluded and not r.vacancy
              and r.employee_id is not None and r.start is not None and r.end is not None),
    )


def revalidate(preview: ImportPreview) -> ImportPreview:
    # Original issues remain immutable audit data. Rebuild current errors from values.
    dynamic = {'UNMATCHED_EMPLOYEE', 'AMBIGUOUS_EMPLOYEE', 'UNKNOWN_POSITION', 'HOURS_DURATION_MISMATCH'}
    issues = [i for i in preview.source_issues if i.severity == IssueSeverity.FATAL
              or (i.severity == IssueSeverity.WARNING and i.code not in dynamic)]
    employee_ids = {e.employee_id for e in preview.roster}
    for wrapped in preview.rows:
        row = wrapped.values
        if row.excluded:
            continue
        for field in row.formula_fields:
            issues.append(issue('FORMULA_VALUE_UNAVAILABLE', row.source_row, field))
        if not row.vacancy:
            for field in row.required_fields_missing:
                issues.append(issue('MISSING_POSITION', row.source_row, field))
        if not row.vacancy and row.employee_id not in employee_ids:
            code = row.match_status if row.match_status in {'UNMATCHED_EMPLOYEE', 'AMBIGUOUS_EMPLOYEE'} else 'UNMATCHED_EMPLOYEE'
            issues.append(issue(code, row.source_row, 'employee_id'))
        if row.start is None or row.end is None:
            issues.append(issue('INVALID_SHIFT_TIME', row.source_row, 'start/end'))
        else:
            if row.start.date() != preview.operational_date:
                issues.append(issue('INVALID_DATE', row.source_row, 'start'))
            if (row.start.utcoffset() is None) != (row.end.utcoffset() is None):
                issues.append(issue('MIXED_DATETIME_AWARENESS', row.source_row, 'start/end'))
            elif row.start >= row.end:
                issues.append(issue('INVALID_SHIFT_RANGE', row.source_row, 'start/end'))
            elif (row.end - row.start).total_seconds() > preview.import_config.maximum_shift_hours * 3600:
                issues.append(issue('IMPLAUSIBLY_LONG_SHIFT', row.source_row, 'end'))
            if row.imported_hours is not None and (row.start.utcoffset() is None) == (row.end.utcoffset() is None):
                if abs((row.end - row.start).total_seconds() / 3600 - row.imported_hours) > preview.import_config.hours_tolerance:
                    issues.append(issue('HOURS_DURATION_MISMATCH', row.source_row, 'hours', warning=True))
        if not row.vacancy and row.normalized_role == OperationalRole.UNKNOWN:
            issues.append(issue('UNKNOWN_POSITION', row.source_row, 'normalized_role', warning=True))
    day = to_day(preview)
    active_rows = [r.values for r in preview.rows if not r.values.excluded and not r.values.vacancy
                   and r.values.employee_id is not None and r.values.start is not None and r.values.end is not None]
    for item in validate_config(preview.config) + validate_operational_day(day, preview.config):
        source_row = None
        if item.path.startswith('employee_shifts['):
            index = int(item.path.split('[', 1)[1].split(']', 1)[0])
            source_row = active_rows[index].source_row
        issues.append(ReviewIssue(item.code, IssueSeverity.ERROR, item.message, source_row, item.path))
    return replace(preview, issues=tuple(issues))


def apply_corrections(preview: ImportPreview, corrections: tuple[RowCorrection, ...]) -> ImportPreview:
    rows = {r.row_id: r for r in preview.rows}
    employees = {e.employee_id: e for e in preview.employees}
    seen = set()
    employee_changes = {}
    errors = []
    for correction in corrections:
        wrapped = rows.get(correction.row_id)
        if wrapped is None:
            errors.append(issue('UNKNOWN_IMPORT_ROW', field='row_id'))
            continue
        row = wrapped.values
        if any(getattr(correction, field) is not None and not isinstance(getattr(correction, field), datetime)
               for field in ('start', 'end')) or any(
            getattr(correction, field) is not None and type(getattr(correction, field)) is not bool
            for field in ('vacancy', 'excluded', 'enabled')
        ):
            errors.append(issue('INVALID_CORRECTION_VALUE', row.source_row))
            continue
        if correction.row_id in seen:
            errors.append(issue('DUPLICATE_CORRECTION_TARGET', row.source_row))
            continue
        seen.add(correction.row_id)
        changes = {}
        for name in ('employee_id', 'start', 'end', 'normalized_role', 'excluded', 'vacancy'):
            value = getattr(correction, name)
            if value is not None:
                changes[name] = value
        if correction.employee_id is not None:
            if row.vacancy and correction.vacancy is not False:
                errors.append(issue('VACANCY_RESTORATION_REQUIRED', row.source_row, 'vacancy'))
                continue
            if correction.employee_id not in employees:
                errors.append(issue('UNKNOWN_SHIFT_EMPLOYEE', row.source_row, 'employee_id'))
                continue
            changes['match_status'] = 'MATCHED'
        if correction.normalized_role is not None and not isinstance(correction.normalized_role, OperationalRole):
            errors.append(issue('INVALID_OPERATIONAL_ROLE', row.source_row, 'normalized_role'))
            continue
        resolved = set(changes)
        if 'employee_id' in resolved:
            resolved.add('employee')
        if 'normalized_role' in resolved:
            resolved.add('position')
        if {'start', 'end'} <= resolved:
            resolved.add('date')
        changes['formula_fields'] = tuple(f for f in row.formula_fields if f not in resolved)
        changes['required_fields_missing'] = tuple(f for f in row.required_fields_missing if f not in resolved)
        row = replace(row, **changes)
        if row.vacancy:
            row = replace(row, employee_id=None, match_status='VACANCY')
        if correction.enabled is not None or correction.qualifications is not None:
            if row.vacancy or row.excluded or row.employee_id not in employees:
                errors.append(issue('EMPLOYEE_OVERRIDE_REQUIRES_ACTIVE_MATCH', row.source_row))
                continue
            overrides = {name: getattr(correction, name) for name in ('enabled', 'qualifications')
                         if getattr(correction, name) is not None}
            if ('enabled' in overrides and type(overrides['enabled']) is not bool) or (
                'qualifications' in overrides and (not isinstance(overrides['qualifications'], frozenset)
                    or any(not isinstance(q, Qualification) for q in overrides['qualifications']))
            ):
                errors.append(issue('INVALID_EMPLOYEE_OVERRIDE', row.source_row))
                continue
            for name, value in overrides.items():
                key = (row.employee_id, name)
                if key in employee_changes and employee_changes[key] != value:
                    errors.append(issue('CONFLICTING_EMPLOYEE_OVERRIDE', row.source_row, name))
                employee_changes[key] = value
            employees[row.employee_id] = replace(employees[row.employee_id], **overrides)
        rows[wrapped.row_id] = replace(wrapped, values=row)
    if errors:
        raise ImportError('INVALID_IMPORT_CORRECTIONS', issues=tuple(errors))
    return revalidate(replace(
        preview, revision=preview.revision + 1, rows=tuple(rows.values()),
        employees=tuple(employees[e.employee_id] for e in preview.employees), corrections=corrections,
    ))
