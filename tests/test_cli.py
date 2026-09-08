"""Command-line adapter tests without duplicating domain reporting logic."""

import subprocess
import sys

import pytest

from ramp_optimizer.cli import build_parser, main


@pytest.mark.parametrize("arguments", [["--help"], ["demo", "--help"], ["benchmark", "--help"]])
def test_help_commands_exit_successfully(arguments, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)

    assert error.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("normal", "Readiness: READY"),
        ("shortage", "Readiness: MANUAL_INTERVENTION_REQUIRED"),
        ("emergency-lead", "Readiness: READY_WITH_WARNINGS"),
    ],
)
def test_demo_scenarios_use_existing_report_and_return_success(
    scenario: str,
    expected: str,
    capsys,
) -> None:
    exit_code = main(
        ["demo", "--scenario", scenario, "--time-limit", "5"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output.startswith("Ramp Team Flight Optimizer\n")
    assert expected in output
    assert output.index("Lead interventions:") < output.index("Warnings:")
    assert "Traceback" not in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["demo", "--scenario", "invalid"],
        ["demo", "--time-limit", "0"],
        ["demo", "--time-limit", "-1"],
        ["demo", "--time-limit", "nan"],
        ["benchmark", "--repeat", "0"],
    ],
)
def test_invalid_cli_usage_has_exit_code_two_without_traceback(
    arguments,
    capsys,
) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "Traceback" not in captured.err


def test_parser_exposes_documented_commands() -> None:
    parser = build_parser()

    assert parser.prog == "ramp-optimizer"
    assert main(["benchmark", "--scenario", "small", "--repeat", "1"]) == 0


def test_module_entry_point_help_smoke() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ramp_optimizer", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "ramp-optimizer" in completed.stdout
    assert "Traceback" not in completed.stderr
