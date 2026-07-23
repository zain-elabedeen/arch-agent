"""Export the FastAPI OpenAPI contract for frontend tooling."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def export_openapi(output: Path) -> None:
    from agent.app.main import app

    output.parent.mkdir(parents=True, exist_ok=True)
    schema: dict[str, Any] = app.openapi()
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export ArchAgent's OpenAPI schema.")
    parser.add_argument(
        "output",
        nargs="?",
        default="docs/openapi.json",
        help="Output path for the generated OpenAPI JSON contract.",
    )
    args = parser.parse_args()
    export_openapi(Path(args.output))


if __name__ == "__main__":
    main()
