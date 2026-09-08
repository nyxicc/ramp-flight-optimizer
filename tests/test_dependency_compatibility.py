"""Compatibility policy regressions for declared runtime versions."""

import importlib.metadata

import ramp_optimizer.optimizer as optimizer_module


def test_ortools_specific_annotations_are_postponed_for_supported_minors() -> None:
    """OR-Tools 9.12 lacks the newer ``CpSolverStatus`` runtime alias."""

    annotation = optimizer_module._map_status.__annotations__["status"]

    assert isinstance(annotation, str)


def test_installed_ortools_is_inside_the_declared_supported_major_range() -> None:
    major, minor, *_ = (
        int(part) for part in importlib.metadata.version("ortools").split(".")
    )

    assert major == 9
    assert minor >= 12
