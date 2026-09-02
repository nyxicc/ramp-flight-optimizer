"""Tests for central optimizer configuration."""

from dataclasses import replace

from ramp_optimizer import (
    OperationalRole,
    OptimizerConfig,
    TeamWorkImportConfig,
    position_mapping,
    validate_config,
    validate_teamwork_import_config,
)


def test_approved_configuration_defaults() -> None:
    config = OptimizerConfig()

    assert config.express_workload_factor == 0.80
    assert config.three_person_workload_multiplier == 1.15
    assert config.continuity_horizon_minutes == 120
    assert config.allow_leads_for_minimum_staffing is False
    assert config.solver_num_search_workers == 1
    assert validate_config(config) == ()


def test_invalid_staffing_relationship_is_reported() -> None:
    config = replace(OptimizerConfig(), normal_preferred_staff=2)

    issues = validate_config(config)

    assert any(issue.code == "INVALID_NORMAL_STAFFING_LEVELS" for issue in issues)


def test_invalid_synthetic_workload_factors_are_reported() -> None:
    config = replace(
        OptimizerConfig(),
        express_workload_factor=1.2,
        three_person_workload_multiplier=0.9,
    )

    codes = {issue.code for issue in validate_config(config)}

    assert "INVALID_EXPRESS_WORKLOAD_FACTOR" in codes
    assert "INVALID_THREE_PERSON_MULTIPLIER" in codes


def test_boolean_is_not_accepted_as_an_integer_configuration_value() -> None:
    config = replace(OptimizerConfig(), required_break_minutes=True)

    issues = validate_config(config)

    assert any(
        issue.path == "config.required_break_minutes" for issue in issues
    )


def test_malformed_staffing_type_returns_issue_instead_of_crashing() -> None:
    config = replace(OptimizerConfig(), minimum_staff="3")  # type: ignore[arg-type]

    issues = validate_config(config)

    assert any(issue.path == "config.minimum_staff" for issue in issues)


def test_default_position_mapping_is_explicit_and_conservative() -> None:
    config = TeamWorkImportConfig()
    mapping = position_mapping(config)

    assert mapping["ramp agent"] is OperationalRole.RAMP_AGENT
    assert mapping["ramp lead"] is OperationalRole.RAMP_LEAD
    assert mapping["ramp trainee"] is OperationalRole.TRAINEE
    assert mapping["ramp instructor"] is OperationalRole.POSSIBLE_RAMP_SUPPORT
    assert mapping["customer service agent"] is OperationalRole.NON_RAMP
    assert mapping["cabin cleaner"] is OperationalRole.NON_RAMP
    assert mapping["bagroom"] is OperationalRole.UNKNOWN
    assert validate_teamwork_import_config(config) == ()
