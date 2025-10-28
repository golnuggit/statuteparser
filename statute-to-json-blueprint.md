
# Statute→JSON (IRC & Treasury Regs) — Transportable Skill Blueprint

This document is a model‑agnostic blueprint you can drop into a Claude **Skill** (or adapt for other LLM systems) that takes unstructured statutory/regulatory text and converts it into clean, linkable JSON, with optional Markdown output.

It includes:

1) A robust JSON data model  
2) A deterministic parsing pipeline (with cross‑reference linking)  
3) A ready‑to‑ship `SKILL.md` for a Claude Skill  
4) Reference Python tool stubs the Skill can call  
5) Validation, QA, and indexing guidance tailored for legal work

Where helpful, the design aligns with public structural standards used for U.S. Code (USLM XML) and CFR (eCFR XML), so your JSON maps cleanly to authoritative hierarchies and future conversions.

---

## 0) Design Goals (Why this works for tax)

- **Deterministic first, LLM‑assisted second.** Use simple, well‑tested parsing rules for U.S. hierarchy markers—(a), (1), (A), (i), (I), (aa), (AA)—and only ask the LLM to resolve edge cases. This keeps outputs consistent and cite‑ready.  
- **USLM/eCFR‑friendly geometry.** Model your JSON so you can later round‑trip to USLM (statutes) or eCFR (regs). Both officially represent hierarchical units and identifiers, so mirroring that structure makes your data portable and auditable.  
- **Explicit internal links.** Convert language like “for purposes of paragraph (a)(2)” into graph edges with precise anchors so models (and humans) don’t miss exceptions and scopes.  
- **Chunk once, use everywhere.** Emit both: (i) a single tree JSON per section/part and (ii) flattened nodes (JSON Lines) for retrieval and evaluation.

---

## 1) Target JSON Model (schema + example)

**High‑level shape (per section / regulation):**

```json
{
  "id": "usc:26:162",
  "type": "statute",
  "source": {
    "work": "USC",
    "title": 26,
    "section": "162",
    "publication": "OLRC/USLM 2024-10-01"
  },
  "breadcrumbs": [
    {"level":"Subtitle","n":"A"},
    {"level":"Chapter","n":"1"},
    {"level":"Subchapter","n":"B"},
    {"level":"Part","n":"VI"},
    {"level":"Subpart","n":"A"}
  ],
  "heading": "Business expenses",
  "nodes": [
    {
      "id": "usc:26:162(a)",
      "label": "(a)",
      "level": "subsection",
      "heading": "In general",
      "text": "Business expenses deductible ...",
      "children": [
        {
          "id": "usc:26:162(a)(1)",
          "label": "(1)",
          "level": "paragraph",
          "heading": null,
          "text": "Ordinary and necessary...",
          "children": [
            {
              "id": "usc:26:162(a)(1)(A)",
              "label": "(A)",
              "level": "subparagraph",
              "text": "Including the cost of ...",
              "children": []
            }
          ]
        }
      ]
    }
  ],
  "links": [
    {
      "source": "usc:26:162(b)(2)",
      "target": "usc:26:162(a)",
      "ref_text": "For purposes of paragraph (a)...",
      "relation": "for-purposes-of",
      "scope": "intra-section",
      "confidence": 0.98
    },
    {
      "source": "usc:26:162(c)(3)",
      "target": "usc:26:274",
      "ref_text": "subject to section 274",
      "relation": "subject-to",
      "scope": "inter-section",
      "confidence": 0.99
    }
  ],
  "spans": [
    {"node": "usc:26:162(a)", "start": 102, "end": 456}
  ],
  "meta": {
    "enacted": "1954-08-16",
    "amended_through": "2025-06-30",
    "editorial_notes": [],
    "version_hash": "sha256:..."
  }
}
```

**Controlled vocabulary for `level`:**  
`section | subsection | paragraph | subparagraph | clause | subclause | item | subitem`.

> Tip (CFR): carry **part** and **subpart** in `breadcrumbs`, matching the typical CFR organization (Title → Subtitle → Chapter → Subchapter → Part → Subpart → Section).

**JSON Schema (Draft 2020‑12) — excerpt** (use for validation):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.org/schemas/statute.schema.json",
  "type": "object",
  "required": ["id","type","source","nodes"],
  "properties": {
    "id": {"type":"string"},
    "type": {"enum":["statute","regulation"]},
    "source": {
      "type":"object",
      "required":["work","title","section"],
      "properties": {
        "work":{"enum":["USC","CFR"]},
        "title":{"type":["integer","string"]},
        "section":{"type":"string"}
      }
    },
    "nodes": {
      "type":"array",
      "items":{"$ref":"#/definitions/node"}
    },
    "links": {
      "type":"array",
      "items":{"$ref":"#/definitions/link"}
    }
  },
  "definitions": {
    "node": {
      "type":"object",
      "required":["id","label","level","text","children"],
      "properties": {
        "id":{"type":"string"},
        "label":{"type":"string"},
        "level":{"enum":["section","subsection","paragraph","subparagraph","clause","subclause","item","subitem"]},
        "heading":{"type":["string","null"]},
        "text":{"type":"string"},
        "children":{"type":"array","items":{"$ref":"#/definitions/node"}}
      }
    },
    "link": {
      "type":"object",
      "required":["source","target","relation","scope"],
      "properties": {
        "source":{"type":"string"},
        "target":{"type":"string"},
        "ref_text":{"type":["string","null"]},
        "relation":{"enum":["for-purposes-of","subject-to","exception-to","definition-of","see-also","notwithstanding"]},
        "scope":{"enum":["intra-section","intra-part","inter-section","inter-title"]},
        "confidence":{"type":"number","minimum":0,"maximum":1}
      }
    }
  }
}
```

---

## 2) Parsing Pipeline (deterministic core + LLM assist)

**Input:** Unstructured statute/reg text including section headers and nested subdivisions.  
**Output:** JSON document conforming to the schema above, plus optional Markdown with wiki‑style links.

### Step A — Normalize
- Canonicalize whitespace, normalize “§” and “Sec.” markers, fix OCR ligatures, and standardize smart quotes.
- Recognize the instrument: **USC** vs **CFR** by header cues (e.g., “26 C.F.R. § 1.162‑1” vs “26 U.S.C. § 162”).
- Capture heading/caption if present.

### Step B — Identify section boundary
- **USC:** match `(^|\\n)\\s*(Sec\\.|§)\\s*([0-9]+[A-Za-z\\-]*)` and capture heading line(s).  
- **CFR:** match title/part/section patterns: `^(\\d+)\\s*CFR\\s*§\\s*([0-9]+\\.[0-9\\-]+)` and heading.

### Step C — Tokenize enumerations and build the tree
Maintain a **stack** of current levels. Each new label either **descends**, **stays**, or **ascends** in the hierarchy based on the enumeration type:

| Level        | Typical form                            |
|--------------|-----------------------------------------|
| subsection   | `(a)`, `(b)`, … then `(aa)`, `(bb)`     |
| paragraph    | `(1)`, `(2)`, …                         |
| subparagraph | `(A)`, `(B)`, … then `(AA)`, `(BB)`     |
| clause       | `(i)`, `(ii)` (lowercase roman)         |
| subclause    | `(I)`, `(II)` (uppercase roman)         |
| item         | `(aa)`, `(bb)` (lowercase double‑alpha) |
| subitem      | `(AA)`, `(BB)` (uppercase double‑alpha) |

**Heuristics:**
- Treat a line beginning with a label + space as a new node: e.g., `"(a) In general."`  
- Lines not starting with a label **append** to the current node’s `text` (account for hanging indents).
- Reset numbering when ascending levels (e.g., after finishing `(1) … (n)` beneath `(a)`, the next `(b)` starts a new sibling subtree).

### Step D — Resolve internal references into explicit links
Recognize patterns like:
- **Relative refs:** “paragraph (a)(2)”, “subparagraph (B)”, “clause (i)”, “this subsection”, “preceding sentence” (the last one is often ambiguous; handle cautiously).
- **Absolute refs:** “section 274”, “section 41(f)(2)”, “§ 1.482‑1(d)(3)”.

**Resolution algorithm (intra‑document):**
1. **Parse noun → level:** “paragraph” → `paragraph`, “subparagraph” → `subparagraph`, etc.  
2. **Interpret the path in parentheses:** `(a)(2)(B)` maps to successive levels: subsection → paragraph → subparagraph.  
3. **Determine the base ancestor:**  
   - If it says “paragraph (B)(i)” without higher levels, anchor at the **nearest ancestor whose level is “paragraph”** and take `(B)(i)` as descendants.  
   - If it says “this paragraph/subsection”, link to the **current node** at that level.  
4. **Construct the `target` id** from the current section’s base id + levels.  
5. If the reference names a **different section** (“subject to section 274”), normalize to `usc:26:274` (or CFR equivalent) and tag `scope:"inter-section"`.

**Relation vocabulary mapping (examples):**
- “For purposes of …” → `for-purposes-of`  
- “Except as provided in …” → `exception-to`  
- “Subject to …” → `subject-to`  
- “Notwithstanding …” → `notwithstanding`  
- “See …” → `see-also`  
- “The term X has the meaning given in …” → `definition-of`

### Step E — Emit JSON + (optional) Markdown
- JSON: as per schema.  
- Markdown: add wiki‑style links like `[[26 USC §162(a)(1)]]` or section‑local anchors `[[§162(a)(1)]]` with id slugs (e.g., `#usc-26-162-a-1`).

### Step F — Validate & QA
- Validate with the provided JSON Schema.  
- Run lint checks (no orphan nodes, labels strictly monotonic within a level, link `target`s exist where `scope` is intra‑document).

---

## 3) Claude Skill Packaging (portable to other LLMs)

Anthropic **Skills** can be a folder with a `SKILL.md` plus any scripts/resources. They can be invoked in Claude Code and via API, with instructions, examples, and tool calls bundled.

**Recommended folder layout**
```
statute-to-json/
├─ SKILL.md
├─ schemas/
│  └─ statute.schema.json
├─ tools/
│  ├─ parse_statute.py
│  └─ resolve_links.py
├─ examples/
│  ├─ input_26usc_162.txt
│  └─ output_26usc_162.json
└─ tests/
   └─ smoke.md
```

### SKILL.md (drop‑in example)

```markdown
---
name: "Statute→JSON (IRC & Treasury Regs)"
version: "0.1.0"
description: >
  Convert unstructured Title 26 statutes and 26 CFR regulations into normalized JSON
  with explicit intra- and inter-text links for cross-references. Deterministic parsing
  first; LLM-assisted fallback for edge cases. Outputs also include optional Markdown with wiki-style links.
authors:
  - "Your Firm · Tax Knowledge Engineering"
intended_use:
  - "Parse sections like '26 U.S.C. § 162' or '26 CFR § 1.162-1' into a hierarchical JSON tree."
  - "Create explicit links for 'for purposes of paragraph (a)(2)' etc."
inputs:
  - name: source_text
    type: string
    required: true
  - name: source_type
    type: string
    enum: ["USC","CFR"]
    required: true
  - name: citation_hint
    type: string
    required: false
outputs:
  - name: json
    type: application/json
  - name: md
    type: text/markdown
tools:
  - id: py.parse_statute
    description: Deterministic tokenizer + tree builder for USC/CFR enumerations.
    entry: ./tools/parse_statute.py
  - id: py.resolve_links
    description: Pattern-matching + scope resolution for relative references.
    entry: ./tools/resolve_links.py
resources:
  - ./schemas/statute.schema.json
  - ./examples/*
security:
  - "No external network. Local execution only."
  - "No PII or client content expected."
---

# Operating Instructions (for the model)

1. **Always call** `py.parse_statute` with `source_text`, `source_type`, and optional `citation_hint`.  
   - If the parser returns a well-formed JSON tree, proceed to step 3.  
   - If it returns `needs_llm_assist: true`, do step 2.

2. **LLM-assist only for ambiguous structure.**  
   - Infer the minimal missing labels/headings **without hallucinating text**.  
   - Produce a corrected tree and continue.

3. Call `py.resolve_links` with the JSON tree to add `links[]`.  
   - Recognize and resolve: `for purposes of`, `except as provided in`, `subject to`, `notwithstanding`, `see`.  
   - Resolve relative refs (“paragraph (B)”) against the nearest ancestor of that level.

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
  "id":"cfr:26:1.162-1",
  "type":"regulation",
  "source":{"work":"CFR","title":26,"section":"1.162-1"},
  "heading":"Business expenses",
  "nodes":[{"id":"cfr:26:1.162-1(a)","label":"(a)","level":"subsection","heading":"In general","text":"Business expenses ...","children":[]}],
  "links":[{"source":"cfr:26:1.162-1(b)","target":"cfr:26:1.162-1(a)","relation":"for-purposes-of","scope":"intra-section","confidence":0.98}]
}
```

# Quality Bar

- Deterministic first; LLM fixes formatting only.
- All nodes must have valid `label`, `level`, and non-empty `text`.
- All intra-section link `target`s must resolve to an existing node id.
- No summaries or paraphrases—**only** verbatim statutory/regulatory text, normalized for whitespace.
```

---

## 4) Reference Python Tools (deterministic core)

Keep these short and battle‑tested. They’re designed so any LLM can orchestrate them.

### `tools/parse_statute.py` (outline)

- **Inputs:** `source_text`, `source_type` (`USC`|`CFR`), `citation_hint` (optional).  
- **Algorithm:**  
  1) Normalize; 2) Identify section header + caption; 3) Tokenize labels; 4) Build a tree using a stack; 5) Emit ids like `usc:26:162(a)(1)(A)`; 6) Return JSON.

Key pieces to implement:

```python
# tools/parse_statute.py
import re
from typing import List, Dict, Any

LABEL_PATTERNS = [
  ("subsection",   r"^\(([a-z]{1,2})\)\s"),
  ("paragraph",    r"^\(([0-9]+)\)\s"),
  ("subparagraph", r"^\(([A-Z]{1,2})\)\s"),
  ("clause",       r"^\(([ivxlcdm]+)\)\s"),
  ("subclause",    r"^\(([IVXLCDM]+)\)\s"),
  ("item",         r"^\(([a-z]{2})\)\s"),
  ("subitem",      r"^\(([A-Z]{2})\)\s")
]
# NOTE: order matters; test longest/most specific patterns earlier when implementing.

LEVEL_ORDER = ["section","subsection","paragraph","subparagraph","clause","subclause","item","subitem"]

def detect_label(line: str):
  for level, pattern in LABEL_PATTERNS:
    m = re.match(pattern, line)
    if m:
      return level, m.group(1), m.end()
  return None, None, 0

def normalize(text: str) -> str:
  text = text.replace("\u00A0", " ").replace("§", "§")
  text = re.sub(r"[ \t]+", " ", text)
  text = re.sub(r"\r\n?", "\n", text).strip()
  return text

def parse(source_text: str, source_type: str, citation_hint: str = "") -> Dict[str, Any]:
  text = normalize(source_text)
  lines = text.split("\n")

  # Extremely simple header detection; replace with robust patterns.
  heading = None
  base_id = None
  if source_type == "CFR":
    # e.g., "26 CFR § 1.162-1 Business expenses."
    m = re.search(r"(?m)^(\d+)\s*CFR\s*§\s*([0-9]+\.[0-9\-]+)\s*(.*)$", text)
    if m:
      title, section, heading = m.group(1), m.group(2), m.group(3).strip() or None
      base_id = f"cfr:{title}:{section}"
  else:
    # USC: "26 U.S.C. § 162 Business expenses."
    m = re.search(r"(?m)^(\d+)\s*(U\.?S\.?C\.?)?\s*§\s*([0-9A-Za-z\-]+)\s*(.*)$", text)
    if m:
      title, section, heading = m.group(1), m.group(3), (m.group(4).strip() or None)
      base_id = f"usc:{title}:{section}"

  nodes = []
  stack = []  # list of dicts representing current path

  def new_node(level, label, heading_text, body_text):
    node_id = base_id if level == "section" else f"{base_id}({label})"
    # For deeper levels, append all labels from stack + current
    if level != "section":
      if stack and stack[0].get("level") == "section":
        # rebuild id from section base + labels in stack (excluding section) + current
        parts = []
        for s in stack[1:]:
          parts.append(f"({s['label']})")
        parts.append(f"({label})")
        node_id = base_id + "".join(parts)
    return {
      "id": node_id,
      "label": f"({label})" if level != "section" else "",
      "level": level,
      "heading": heading_text,
      "text": body_text.strip(),
      "children": []
    }

  # Seed a section node if we have heading
  if base_id:
    section_node = {
      "id": base_id,
      "label": "",
      "level": "section",
      "heading": heading,
      "text": "",
      "children": []
    }
    nodes.append(section_node)
    stack = [section_node]
  else:
    # Fallback: assume a section without explicit header
    base_id = "unknown:0:0"
    section_node = {
      "id": base_id,
      "label": "",
      "level": "section",
      "heading": None,
      "text": "",
      "children": []
    }
    nodes.append(section_node)
    stack = [section_node]

  current = section_node

  for raw in lines:
    line = raw.strip()
    if not line:
      continue
    lvl, lab, cut = detect_label(line)
    if lvl:
      # ascend/descend based on LEVEL_ORDER
      # find target parent level index
      target_idx = LEVEL_ORDER.index(lvl)
      while stack and LEVEL_ORDER.index(stack[-1]["level"]) >= target_idx:
        stack.pop()
      parent = stack[-1] if stack else section_node
      # heading if there's a sentence-like fragment right after label ending with period
      body = line[cut:]
      heading_text = None
      # simple heading detection: "In general." at start
      mhead = re.match(r"^([A-Z][^.]+?\.)\s*(.*)$", body)
      if mhead:
        heading_text = mhead.group(1).strip()
        body = mhead.group(2)
      node = new_node(lvl, lab, heading_text, body)
      parent["children"].append(node)
      stack.append(node)
      current = node
    else:
      # append to current node text
      current["text"] = (current.get("text","") + (" " if current.get("text") else "") + line).strip()

  return {
    "id": base_id,
    "type": "regulation" if base_id.startswith("cfr:") else "statute",
    "source": {},
    "heading": section_node.get("heading"),
    "nodes": [section_node],
    "links": [],
    "meta": {}
  }
```

### `tools/resolve_links.py` (outline)

```python
# tools/resolve_links.py
import re
from typing import Dict, Any, List

REL_CUES = [
  ("for-purposes-of", r"\bfor purposes of\b"),
  ("exception-to",    r"\bexcept as provided in\b"),
  ("subject-to",      r"\bsubject to\b"),
  ("notwithstanding", r"\bnotwithstanding\b"),
  ("see-also",        r"\bsee\b")
]

ABS_REF = re.compile(r"\bsection[s]?\s+([0-9A-Za-z\.\-]+)(\([^)]+\))*", re.IGNORECASE)
REL_REF = re.compile(r"\b(paragraph|subparagraph|clause|subclause|subsection)\s+((\([^)]+\))+)", re.IGNORECASE)
THIS_REF = re.compile(r"\bthis\s+(paragraph|subparagraph|clause|subclause|subsection)\b", re.IGNORECASE)

LEVEL_ORDER = ["section","subsection","paragraph","subparagraph","clause","subclause","item","subitem"]

def flatten_nodes(node: Dict[str, Any]) -> List[Dict[str, Any]]:
  out = []
  def walk(n):
    out.append(n)
    for c in n.get("children", []):
      walk(c)
  walk(node)
  return out

def nearest_ancestor_of_level(path: List[Dict[str, Any]], level: str) -> Dict[str, Any]:
  for n in reversed(path):
    if n.get("level") == level:
      return n
  return path[0]  # section fallback

def resolve(json_doc: Dict[str, Any]) -> Dict[str, Any]:
  base_id = json_doc["id"]
  section = json_doc["nodes"][0]
  flat = flatten_nodes(section)
  id_to_node = {n["id"]: n for n in flat}

  links = []

  def add_link(source_id, target_id, relation, scope, ref_text, conf=0.95):
    links.append({
      "source": source_id,
      "target": target_id,
      "relation": relation,
      "scope": scope,
      "ref_text": ref_text,
      "confidence": conf
    })

  # Build a map of node paths for ancestor lookup
  def build_paths(n, path, paths):
    me = path + [n]
    paths[n["id"]] = me
    for c in n.get("children", []):
      build_paths(c, me, paths)

  paths = {}
  build_paths(section, [], paths)

  for n in flat:
    text = n.get("text","")
    # relation cue
    relation = None
    for rel, rx in REL_CUES:
      if re.search(rx, text, re.IGNORECASE):
        relation = rel
        break
    if not relation:
      relation = "see-also" if (ABS_REF.search(text) or REL_REF.search(text) or THIS_REF.search(text)) else None

    # relative refs
    for m in REL_REF.finditer(text):
      noun = m.group(1).lower()         # e.g., 'paragraph'
      paren = m.group(2)                # e.g., '(a)(2)'
      # build target id
      # find base ancestor of that level
      anc = nearest_ancestor_of_level(paths[n["id"]], noun)
      # construct suffix from paren groups
      suffix = "".join(re.findall(r"\([^)]+\)", paren))
      target = base_id + suffix
      add_link(n["id"], target, relation or "see-also", "intra-section", m.group(0))

    # absolute refs
    for m in ABS_REF.finditer(text):
      sec = m.group(1)  # e.g., 274, 41, 41(f)(2)
      parens = m.group(2) or ""
      target = f"usc:26:{sec}{parens}" if base_id.startswith("usc:26:") else f"usc:26:{sec}{parens}"
      add_link(n["id"], target, relation or "see-also", "inter-section", m.group(0))

    # 'this paragraph/subsection'
    for m in THIS_REF.finditer(text):
      noun = m.group(1).lower()
      anc = nearest_ancestor_of_level(paths[n["id"]], noun)
      add_link(n["id"], anc["id"], relation or "see-also", "intra-section", m.group(0), conf=0.9)

  json_doc["links"] = links
  return json_doc
```

---

## 5) Worked Example (abridged)

**Input snippet (illustrative only; use official text in practice):**
```
26 CFR § 1.162-1 Business expenses.
(a) In general. Business expenses deductible from gross income include ...
(b) For purposes of paragraph (a), the following exceptions apply: ...
```

**Output (abridged, showing the link):**
```json
{
  "id":"cfr:26:1.162-1",
  "type":"regulation",
  "source":{"work":"CFR","title":26,"section":"1.162-1"},
  "heading":"Business expenses",
  "nodes":[
    {"id":"cfr:26:1.162-1(a)","label":"(a)","level":"subsection","heading":"In general","text":"Business expenses ...","children":[]},
    {"id":"cfr:26:1.162-1(b)","label":"(b)","level":"subsection","text":"For purposes of paragraph (a), ...","children":[]}
  ],
  "links":[
    {"source":"cfr:26:1.162-1(b)","target":"cfr:26:1.162-1(a)","ref_text":"For purposes of paragraph (a)","relation":"for-purposes-of","scope":"intra-section","confidence":0.98}
  ]
}
```

---

## 6) Validation, QA, and Gold Sources

- **Validation:** JSON Schema + unit tests on tricky patterns (nested roman numerals; double‑letters; “this subsection” deictics; multiple refs in a line).  
- **Authoritative structure:**  
  - **USLM XML (U.S. Code)** for section/subdivision semantics and future conversions.  
  - **eCFR XML** and standard CFR organization (Title → Subtitle → Chapter → Subchapter → Part → Subpart → Section).

*(Law text is public domain; still, prefer official feeds for accuracy and versioning.)*

---

## 7) Indexing & Retrieval for LLMs (precision citations)

- Export a **node‑per‑row** JSONL (or Parquet) with fields: `id`, `parent_id`, `cite`, `breadcrumb`, `level`, `label`, `heading`, `text`, `links_out`, `links_in`.  
- Chunk boundaries should **exactly** follow nodes (never split mid‑node).  
- Store a `display_cite`: e.g., `"26 U.S.C. § 162(a)(1)(A)"` or `"26 C.F.R. § 1.162‑1(b)"`.  
- During RAG, constrain retrieval to **same section** first; then **linked sections** via `links[]` (e.g., exceptions, definitions).

---

## 8) Extensions

- **Definitions graph:** If a node contains “The term X means…”, create a `definition-of` link to that node and tag `meta.defines: ["X"]`.  
- **Editorial notes / effective dates:** Track with `meta.effective_from`, `meta.termination_on` where available.  
- **Cross‑title hops:** Normalize to canonical ids like `usc:26:41(f)(2)` or `cfr:26:1.482-1(d)(3)`; carry both machine id and display cite.

---

## 9) MVP Build Plan (≈90 minutes)

1. Implement `parse_statute.py` with the stack‑based tokenizer.  
2. Implement `resolve_links.py` with relative/absolute patterns and nearest‑ancestor logic.  
3. Validate against `statute.schema.json`.  
4. Package into `SKILL.md` above and run smoke tests on § 162, § 274, § 41, and regs § 1.482‑1.  
5. Add a golden set of 20 snippets with expected JSON (unit tests).

---

### Notes for Agents

- **Never fabricate legal text.** The Skill may fix labels/levels but must only emit verbatim statutory/regulatory content (normalized for whitespace).  
- **No external network during runs.** Feed the text and context files explicitly.  
- **Deterministic precedence.** If parser and LLM disagree on structure, prefer the parser and ask for human review.
