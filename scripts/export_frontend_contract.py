"""Export the existing API schema for the dashboard's generated TypeScript types."""

import json
from pathlib import Path

from ramp_optimizer_api.app import app

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"
    target.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
