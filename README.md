# statuteparser

Utilities for converting Title 26 statutes and Treasury regulations into normalized JSON suitable for retrieval and downstream automation.

## Contents

- `statute-to-json-blueprint.md`: high-level design for the Statute→JSON Claude Skill.
- `statute_to_json/`: implementation of the blueprint, packaged as a Claude Skill with deterministic parsing and link resolution tools.

## Getting Started

1. Install dependencies (standard library only).
2. Parse a source file:
   ```bash
   python -m statute_to_json.tools.parse_statute statute_to_json/examples/input_26usc_162.txt --source-type USC
   ```
   To ingest the official USLM XML instead of plain text, set `--input-format uslm` and pass a citation hint so the parser can
   derive canonical identifiers:
   ```bash
   python -m statute_to_json.tools.parse_statute path/to/26usc162.xml --source-type USC --input-format uslm --citation-hint "26 U.S.C. § 162"
   ```
3. Resolve links on the emitted JSON:
   ```bash
   python -m statute_to_json.tools.resolve_links statute_to_json/examples/output_26usc_162.json
   ```
   During this step the resolver both emits structured `links[]` and rewrites the referenced phrases in each node's `text` with
   `[[target-id]]` annotations (for example, `paragraph (a) [[usc:26:162(a)]]`) so cross-references are explicit in downstream
   consumers.

See `statute_to_json/tests/smoke.md` for manual smoke tests and `statute_to_json/examples/` for sample inputs and outputs.

### Tests

Run the Skill's regression test suite (referenced from `SKILL.md`) with:

```bash
python -m unittest discover -s tests -p 'test_pipeline.py'
```

Optional: install `jsonschema` to enable the validation helper (`pip install jsonschema`).

### Branch alignment

The canonical Skill implementation now lives on the `main` branch so downstream automation can track the latest parser and link
resolver behavior without switching branches.
