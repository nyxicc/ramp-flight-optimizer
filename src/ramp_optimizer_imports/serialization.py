"""Version 1 canonical review JSON, shared by persistence and API mapping."""

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import json

from ramp_optimizer.config import OptimizerConfig, TeamWorkImportConfig
from ramp_optimizer.enums import IssueSeverity, OperationalRole, Qualification
from ramp_optimizer.models import Employee, ScheduleReviewRow
from ramp_optimizer_imports.models import ImportPreview, ReviewIssue, ReviewRow, RowCorrection


def json_value(value):
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(json_value(v) for v in value)
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    return value


def canonical_json(value) -> str:
    return json.dumps(json_value(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def load_preview(serialized: str) -> ImportPreview:
    value = json.loads(serialized)

    def employee(item):
        return Employee(**{**item, 'qualifications': frozenset(Qualification(q) for q in item['qualifications'])})

    def parsed_row(item):
        return ScheduleReviewRow(**{
            **item, 'normalized_role': OperationalRole(item['normalized_role']),
            'start': datetime.fromisoformat(item['start']) if item['start'] else None,
            'end': datetime.fromisoformat(item['end']) if item['end'] else None,
            'formula_fields': tuple(item['formula_fields']),
            'required_fields_missing': tuple(item['required_fields_missing']),
            'source_date': date.fromisoformat(item['source_date']) if item['source_date'] else None,
        })

    def issue(item):
        return ReviewIssue(**{**item, 'severity': IssueSeverity(item['severity'])})

    def correction(item):
        return RowCorrection(**{
            **item,
            'normalized_role': OperationalRole(item['normalized_role']) if item['normalized_role'] else None,
            'start': datetime.fromisoformat(item['start']) if item['start'] else None,
            'end': datetime.fromisoformat(item['end']) if item['end'] else None,
            'qualifications': frozenset(Qualification(q) for q in item['qualifications']) if item['qualifications'] is not None else None,
        })

    settings = value['import_config']
    return ImportPreview(
        revision=value['revision'], operational_date=date.fromisoformat(value['operational_date']),
        roster=tuple(employee(e) for e in value['roster']), employees=tuple(employee(e) for e in value['employees']),
        rows=tuple(ReviewRow(r['row_id'], parsed_row(r['values'])) for r in value['rows']),
        source_issues=tuple(issue(i) for i in value['source_issues']), issues=tuple(issue(i) for i in value['issues']),
        config=OptimizerConfig(**value['config']),
        import_config=TeamWorkImportConfig(**{
            **settings, 'position_role_mappings': tuple((label, OperationalRole(role)) for label, role in settings['position_role_mappings']),
            'vacancy_position_placeholders': frozenset(settings['vacancy_position_placeholders']),
        }), corrections=tuple(correction(c) for c in value['corrections']),
    )


def preview_counts(preview):
    blocking_rows = {i.source_row for i in preview.issues if i.blocks_confirmation and i.source_row is not None}
    return {
        'fatal_count': sum(i.severity == IssueSeverity.FATAL for i in preview.issues),
        'error_count': sum(i.severity == IssueSeverity.ERROR for i in preview.issues),
        'warning_count': sum(i.severity == IssueSeverity.WARNING for i in preview.issues),
        'accepted_shift_count': sum(not r.values.excluded and not r.values.vacancy
                                    and r.values.source_row not in blocking_rows for r in preview.rows),
        'vacancy_count': sum(r.values.vacancy and not r.values.excluded for r in preview.rows),
        'unresolved_row_count': len(blocking_rows),
    }
