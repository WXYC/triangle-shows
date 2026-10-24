"""Golden snapshot of the EventResponse contract.

Backend-Service vendors the same shape at tests/fixtures/triangle-shows/event-response.schema.json
(written by its scripts/refresh-triangle-shows-contract.ts), so any change to EventResponse
shows up here as a golden-file diff in the PR that makes it. The serialization mirrors that
script: only `properties` and `required`, top-level keys sorted, two-space JSON, trailing newline.
"""

import json
import os
from pathlib import Path

from app.main import app

GOLDEN = Path(__file__).parent / "fixtures" / "event-response.schema.json"
REGENERATE = "UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_event_response_golden.py"


def _render_event_response_schema() -> str:
    component = app.openapi()["components"]["schemas"]["EventResponse"]
    snapshot = {
        "properties": dict(sorted(component["properties"].items())),
        "required": sorted(component.get("required", [])),
    }
    return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"


def test_event_response_matches_golden_file():
    rendered = _render_event_response_schema()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(rendered)
    assert GOLDEN.read_text() == rendered, (
        "EventResponse no longer matches backend/tests/fixtures/event-response.schema.json. "
        f"If the change is intended, regenerate it with `{REGENERATE}` and commit the diff; "
        "after deploying, refresh Backend-Service's vendored copy "
        "(npx tsx scripts/refresh-triangle-shows-contract.ts <base-url>)."
    )
