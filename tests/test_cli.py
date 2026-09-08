"""Command-line adapter tests without duplicating domain reporting logic."""

import json
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
    ("scenario", "time_limit", "expected"),
    [
        ("normal", "30", "Readiness: READY"),
        ("shortage", "5", "Readiness: MANUAL_INTERVENTION_REQUIRED"),
        ("emergency-lead", "5", "Readiness: READY_WITH_WARNINGS"),
    ],
)
def test_demo_scenarios_use_existing_report_and_return_success(
    scenario: str,
    time_limit: str,
    expected: str,
    capsys,
) -> None:
    exit_code = main(
        ["demo", "--scenario", scenario, "--time-limit", time_limit]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output.startswith("Ramp Team Flight Optimizer\n")
    assert expected in output
    assert output.index("Lead interventions:") < output.index("Warnings:")
    assert "Traceback" not in output
    if scenario == "normal":
        assert "Readiness: MANUAL_INTERVENTION_REQUIRED" not in output
        assert "minimum staffed 24/24" in output


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


def test_benchmark_cli_writes_only_to_explicit_output(tmp_path, capsys) -> None:
    output = tmp_path / "benchmark.json"

    exit_code = main(
        [
            "benchmark",
            "--scenario",
            "small",
            "--repeat",
            "1",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 1
    assert capsys.readouterr().out == f"Benchmark results written to {output}\n"


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
