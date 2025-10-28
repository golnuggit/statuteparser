---
name: "Statute→JSON (IRC & Treasury Regs)"
version: "0.1.0"
skill_spec_version: 1
description: >
  Convert unstructured Title 26 statutes and 26 CFR regulations into normalized JSON
  with explicit intra- and inter-text links for cross-references. Deterministic parsing
  first; LLM-assisted fallback for edge cases. Outputs also include optional Markdown with wiki-style links.
authors:
  - "Your Firm · Tax Knowledge Engineering"
tags:
  - legal
  - parsing
  - structured-data
requirements:
  python:
    runtime: "3.11"
    packages: []
license: "CC0-1.0"
intended_use:
  - "Parse sections like '26 U.S.C. § 162' or '26 CFR § 1.162-1' into a hierarchical JSON tree."
  - "Create explicit links for 'for purposes of paragraph (a)(2)' etc."
inputs:
  - id: source_text
    description: Raw statutory or regulatory text for a single section.
    required: true
    schema:
      type: string
  - id: source_type
    description: Jurisdiction of the source text (U.S. Code or Treasury regulation).
    required: true
    schema:
      type: string
      enum: ["USC", "CFR"]
  - id: citation_hint
    description: Optional canonical citation (e.g., "26 U.S.C. § 162").
    required: false
    schema:
      type: string
outputs:
  - id: json
    description: Normalized JSON document that conforms to `schemas/statute.schema.json`.
    schema:
      type: object
  - id: md
    description: Markdown rendering of the section with wiki-style cross-reference links.
    required: false
    schema:
      type: string
tools:
  - id: py.parse_statute
    name: "Parse statute"
    type: python
    runtime: python3.11
    entrypoint: ./tools/parse_statute.py
    description: Deterministic tokenizer + tree builder for USC/CFR enumerations (supports raw text and USLM XML input).
  - id: py.resolve_links
    name: "Resolve links"
    type: python
    runtime: python3.11
    entrypoint: ./tools/resolve_links.py
    description: Pattern-matching + scope resolution for relative references.
resources:
  - path: ./schemas/statute.schema.json
  - path: ./examples/input_26usc_162.txt
  - path: ./examples/output_26usc_162.json
security:
  - "No external network. Local execution only."
  - "No PII or client content expected."
tests:
  - description: End-to-end parsing and link resolution on sample text.
    command: python -m unittest discover -s tests -p 'test_pipeline.py'
---

# Operating Instructions (for the model)

1. **Always call** `py.parse_statute` with `source_text`, `source_type`, and optional `citation_hint`.
   - If the parser returns a well-formed JSON tree, proceed to step 3.
   - If it returns `needs_llm_assist: true`, do step 2.
   - Provide USLM XML when available—the parser will honor `citation_hint` to derive canonical IDs.

2. **LLM-assist only for ambiguous structure.**
   - Infer the minimal missing labels/headings **without hallucinating text**.
   - Produce a corrected tree and continue.

3. Call `py.resolve_links` with the JSON tree to add `links[]`.
   - Recognize and resolve: `for purposes of`, `except as provided in`, `subject to`, `notwithstanding`, `see`.
   - Resolve relative refs (“paragraph (B)”) against the nearest ancestor of that level.
   - The resolver rewrites the triggering phrases inside each node’s `text` with `[[target-id]]` annotations so cross-references are explicit.

4. Validate the final JSON against `schemas/statute.schema.json`. If invalid, fix strictness issues only (labels, levels, ids). **Never fabricate legal text.**

5. Return:
   - `json`: The validated JSON.
   - `md`: An optional Markdown rendering where cross-references are `[[linked]]` to local anchors.

# Examples

**Input (snippet):**
```
Sec. 1.162-1 Business expenses.
(a) In general. Business expenses deductible from gross income include ...
(b) For purposes of paragraph (a), the following exceptions apply: ...
```

**Output (abridged):**
```json
{
  "id": "cfr:26:1.162-1",
  "type": "regulation",
  "source": {"work": "CFR", "title": 26, "section": "1.162-1"},
  "heading": "Business expenses",
  "nodes": [
    {
      "id": "cfr:26:1.162-1(a)",
      "label": "(a)",
      "level": "subsection",
      "heading": "In general",
      "text": "Business expenses ...",
      "children": []
    }
  ],
  "links": [
    {
      "source": "cfr:26:1.162-1(b)",
      "target": "cfr:26:1.162-1(a)",
      "relation": "for-purposes-of",
      "scope": "intra-section",
      "confidence": 0.98
    }
  ]
}
```

# Deployment Notes

- Follow the [Claude Skills cookbook](https://github.com/anthropics/claude-cookbooks/tree/main/skills) conventions when packaging this folder.
- Publish the `schemas/` directory alongside the Skill so validation remains local and offline.
- Include `tests/` in your deployment archive so the regression command listed above is easy to run.
