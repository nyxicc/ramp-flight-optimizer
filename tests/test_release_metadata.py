"""Release documentation and packaging smoke checks."""

from importlib.metadata import version
from pathlib import Path
import tomllib

import ramp_optimizer


REPOSITORY_ROOT = Path(__file__).parents[1]


def test_package_metadata_and_console_target_match_importable_project() -> None:
    metadata = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert ramp_optimizer.__name__ == "ramp_optimizer"
    assert metadata["requires-python"] == ">=3.12"
    assert metadata["scripts"]["ramp-optimizer"] == "ramp_optimizer.cli:main"
    assert version(metadata["name"]) == metadata["version"]


def test_release_documentation_exists_and_public_commands_are_real() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    required_documents = (
        "CHANGELOG.md",
        "docs/ARCHITECTURE.md",
        "docs/DOMAIN_RULES.md",
        "docs/OPTIMIZATION_OBJECTIVES.md",
        "docs/OPERATIONAL_READINESS.md",
        "benchmarks/README.md",
    )

    assert "ramp-optimizer demo --scenario normal" in readme
    assert "python -m ramp_optimizer benchmark --repeat 3" in readme
    assert "python -m pytest" in readme
    assert all((REPOSITORY_ROOT / path).is_file() for path in required_documents)
