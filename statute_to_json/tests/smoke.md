# Smoke Test Checklist

1. Run `python -m statute_to_json.tools.parse_statute statute_to_json/examples/input_26usc_162.txt --source-type USC` and ensure `needs_llm_assist` is `false`.
2. Run `python -m statute_to_json.tools.resolve_links statute_to_json/examples/output_26usc_162.json` to verify cross-reference linking.
3. Validate the emitted JSON with `python -m statute_to_json.validation` (see README for helper script).
