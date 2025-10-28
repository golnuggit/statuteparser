"""Lightweight validation helpers for the Statute→JSON skill."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "statute.schema.json"


def load_schema() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def validate_document(document: Dict[str, Any]) -> None:
    if jsonschema is None:
        raise RuntimeError("jsonschema package is required for validation")
    schema = load_schema()
    jsonschema.validate(document, schema)


def validate_file(path: str) -> None:
    doc = json.loads(Path(path).read_text())
    validate_document(doc)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate Statute→JSON documents against the schema")
    parser.add_argument("input", help="Path to a JSON file to validate")
    args = parser.parse_args()
    validate_file(args.input)
    print("Validation succeeded")


if __name__ == "__main__":
    main()
