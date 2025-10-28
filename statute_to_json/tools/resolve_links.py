"""Resolve cross-reference links within parsed statute/regulation JSON."""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, Iterable, List, Tuple

RELATION_CUES: List[Tuple[str, str]] = [
    ("for-purposes-of", r"for purposes of"),
    ("subject-to", r"subject to"),
    ("exception-to", r"except as provided"),
    ("notwithstanding", r"notwithstanding"),
    ("definition-of", r"the term .+ means"),
]

ABS_REF = re.compile(r"\bsection\s+([0-9A-Za-z\.\-]+)(\([^)]+\))?", re.IGNORECASE)
REL_REF = re.compile(r"\b(paragraph|subparagraph|clause|subclause|subsection)\s+((\([^)]+\))+)", re.IGNORECASE)
THIS_REF = re.compile(r"\bthis\s+(paragraph|subparagraph|clause|subclause|subsection)\b", re.IGNORECASE)

LEVEL_ORDER = ["section", "subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem"]

EXPECTED_LABEL_PREDICATES = {
    "subsection": lambda value: value.isalpha() and value.islower(),
    "paragraph": lambda value: value.isdigit(),
    "subparagraph": lambda value: value.isalpha() and value.isupper(),
    "clause": lambda value: bool(re.fullmatch(r'[ivxlcdm]+', value.lower())),
    "subclause": lambda value: bool(re.fullmatch(r'[IVXLCDM]+', value)),
    "item": lambda value: value.isalpha() and value.islower() and len(value) == 2,
    "subitem": lambda value: value.isalpha() and value.isupper() and len(value) == 2,
}

def flatten_nodes(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def walk(n: Dict[str, Any]) -> None:
        items.append(n)
        for child in n.get("children", []):
            walk(child)

    walk(node)
    return items


def build_paths(node: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    paths: Dict[str, List[Dict[str, Any]]] = {}

    def walk(n: Dict[str, Any], path: List[Dict[str, Any]]) -> None:
        current_path = path + [n]
        paths[n["id"]] = current_path
        for child in n.get("children", []):
            walk(child, current_path)

    walk(node, [])
    return paths


def nearest_ancestor_of_level(path: Iterable[Dict[str, Any]], level: str) -> Dict[str, Any] | None:
    path_list = list(path)
    for node in reversed(path_list):
        if node.get("level") == level:
            return node
    return path_list[-1] if path_list else None


def detect_relation(text: str) -> str | None:
    for relation, pattern in RELATION_CUES:
        if re.search(pattern, text, re.IGNORECASE):
            return relation
    if ABS_REF.search(text) or REL_REF.search(text) or THIS_REF.search(text):
        return "see-also"
    return None


def build_target_id(base_id: str, base_path: List[Dict[str, Any]], suffix_labels: List[str], noun: str) -> str:
    base_labels = [n["label"].strip("()") for n in base_path if n.get("label")]
    predicate = EXPECTED_LABEL_PREDICATES.get(noun)
    if predicate and suffix_labels:
        first = suffix_labels[0]
        if not predicate(first):
            base_labels = []
    if base_labels and suffix_labels and suffix_labels[0] == base_labels[-1]:
        base_labels = base_labels[:-1]
    labels = base_labels + suffix_labels
    return base_id + "".join(f"({label})" for label in labels)


def resolve(json_doc: Dict[str, Any]) -> Dict[str, Any]:
    if not json_doc.get("nodes"):
        json_doc.setdefault("links", [])
        return json_doc

    root = json_doc["nodes"][0]
    base_id = json_doc.get("id", "")

    flat_nodes = flatten_nodes(root)
    paths = build_paths(root)
    source_info = json_doc.get("source", {})
    source_work = str(source_info.get("work", "USC")).upper()
    source_title = str(source_info.get("title", "")).strip()

    links: List[Dict[str, Any]] = []

    def add_link(source_id: str, target_id: str, relation: str, scope: str, ref_text: str, confidence: float = 0.95) -> None:
        links.append(
            {
                "source": source_id,
                "target": target_id,
                "relation": relation,
                "scope": scope,
                "ref_text": ref_text,
                "confidence": confidence,
            }
        )

    for node in flat_nodes:
        text = node.get("text", "")
        if not text:
            continue
        relation = detect_relation(text) or "see-also"
        replacements: List[Tuple[int, int, str]] = []

        for match in REL_REF.finditer(text):
            noun = match.group(1).lower()
            paren = match.group(2)
            path = paths.get(node["id"], [])
            ancestor = nearest_ancestor_of_level(path, noun)
            ancestor_path = paths.get(ancestor["id"], []) if ancestor else []
            base_path = ancestor_path[:-1] if ancestor_path else []
            suffix_labels = re.findall(r"\(([^)]+)\)", paren)
            target_id = build_target_id(base_id, base_path, suffix_labels, noun)
            add_link(node["id"], target_id, relation, "intra-section", match.group(0))
            replacements.append(
                (match.start(), match.end(), f"{match.group(0)} [[{target_id}]]")
            )

        for match in THIS_REF.finditer(text):
            noun = match.group(1).lower()
            path = paths.get(node["id"], [])
            ancestor = nearest_ancestor_of_level(path, noun)
            if ancestor:
                add_link(
                    node["id"],
                    ancestor["id"],
                    relation,
                    "intra-section",
                    match.group(0),
                    confidence=0.9,
                )
                replacements.append(
                    (match.start(), match.end(), f"{match.group(0)} [[{ancestor['id']}]]")
                )

        for match in ABS_REF.finditer(text):
            section = match.group(1)
            parens = match.group(2) or ""
            prefix = "usc"
            if source_work == "CFR":
                prefix = "cfr"
            target_parts = [prefix]
            if source_title:
                target_parts.append(source_title)
            target_parts.append(f"{section}{parens}")
            target_id = ":".join(target_parts)
            add_link(node["id"], target_id, relation, "inter-section", match.group(0))
            replacements.append(
                (match.start(), match.end(), f"{match.group(0)} [[{target_id}]]")
            )

        if replacements:
            new_text = text
            for start, end, replacement in sorted(replacements, key=lambda x: x[0], reverse=True):
                new_text = new_text[:start] + replacement + new_text[end:]
            node["text"] = new_text

    json_doc["links"] = links
    return json_doc


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve intra- and inter-section links in statute JSON")
    parser.add_argument("input", help="Path to JSON file or '-' for stdin")
    args = parser.parse_args()

    if args.input == "-":
        data = json.loads(sys.stdin.read())
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)

    resolved = resolve(data)
    print(json.dumps(resolved, indent=2))


if __name__ == "__main__":
    main()
