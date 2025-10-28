"""Deterministic tokenizer and tree builder for Title 26 statutes and regulations."""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

LABEL_PATTERNS: List[Tuple[str, str]] = [
    ("item", r"^\(([a-z]{2})\)\s"),
    ("subitem", r"^\(([A-Z]{2})\)\s"),
    ("clause", r"^\(([ivxlcdm]+)\)\s"),
    ("subclause", r"^\(([IVXLCDM]+)\)\s"),
    ("paragraph", r"^\(([0-9]+)\)\s"),
    ("subparagraph", r"^\(([A-Z])\)\s"),
    ("subsection", r"^\(([a-z])\)\s"),
]

LEVEL_ORDER = ["section", "subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem"]

USLM_NS = {"uslm": "http://xml.house.gov/schemas/uslm/1.0"}
USLM_LEVEL_MAP = {
    "subsection": "subsection",
    "paragraph": "paragraph",
    "subparagraph": "subparagraph",
    "clause": "clause",
    "subclause": "subclause",
    "item": "item",
    "subitem": "subitem",
}

HEADER_CFR = re.compile(r"(?m)^(\d+)\s*CFR\s*§\s*([0-9]+\.[0-9\-]+)\s*(.*)$")
HEADER_USC = re.compile(r"(?m)^(\d+)\s*(U\.?S\.?C\.?)?\s*§\s*([0-9A-Za-z\-]+)\s*(.*)$")

@dataclass
class ParseResult:
    document: Dict[str, Any]
    needs_llm_assist: bool = False


def normalize(text: str) -> str:
    """Normalize whitespace and control characters."""
    text = text.replace("\u00A0", " ")
    text = re.sub(r"[\t\f\r]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\r?\n", "\n", text)
    return text.strip()


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def metadata_from_hint(
    citation_hint: str,
    source_type: str,
) -> Tuple[str, str, Dict[str, Any], Optional[str]]:
    hint = citation_hint.strip()
    if hint:
        colon_parts = hint.split(":")
        if len(colon_parts) >= 3 and colon_parts[0].lower() in {"usc", "cfr"}:
            prefix = colon_parts[0].lower()
            title_part = colon_parts[1]
            section_part = ":".join(colon_parts[2:])
            base_id = f"{prefix}:{title_part}:{section_part}"
            doc_type = "statute" if prefix == "usc" else "regulation"
            title_value: Any = title_part
            if title_part.isdigit():
                title_value = int(title_part)
            source = {"work": prefix.upper(), "title": title_value, "section": section_part}
            return base_id, doc_type, source, None

    if source_type.upper() == "CFR":
        match_cfr = HEADER_CFR.search(hint)
        if match_cfr:
            title, section, heading_line = match_cfr.group(1), match_cfr.group(2), match_cfr.group(3).strip() or None
            base_id = f"cfr:{title}:{section}"
            source = {"work": "CFR", "title": int(title), "section": section}
            return base_id, "regulation", source, heading_line

    match_usc = HEADER_USC.search(hint)
    if match_usc:
        title, section, heading_line = match_usc.group(1), match_usc.group(3), match_usc.group(4).strip() or None
        base_id = f"usc:{title}:{section}"
        source = {"work": "USC", "title": int(title), "section": section}
        return base_id, "statute", source, heading_line

    prefix = "usc" if source_type.upper() == "USC" else "cfr"
    base_id = f"{prefix}:{hint or 'unknown'}"
    source = {"work": prefix.upper(), "title": hint or "", "section": hint or ""}
    return base_id, ("statute" if prefix == "usc" else "regulation"), source, None


def detect_label(line: str) -> Tuple[Optional[str], Optional[str], int]:
    for level, pattern in LABEL_PATTERNS:
        match = re.match(pattern, line)
        if match:
            return level, match.group(1), match.end()
    return None, None, 0


def heading_and_body(body: str) -> Tuple[Optional[str], str]:
    """Split a potential heading from the rest of the body."""
    candidate = body.strip()
    if not candidate:
        return None, ""
    # Heuristic: heading is short (<80 chars) and followed by a period + space
    parts = re.split(r"\.\s+", candidate, maxsplit=1)
    if len(parts) == 2:
        heading_candidate, rest = parts
        heading_candidate = heading_candidate.strip()
        if heading_candidate and len(heading_candidate) <= 80 and heading_candidate[0].isupper():
            return heading_candidate, rest.strip()
    return None, candidate


def make_node_id(base_id: str, stack: List[Dict[str, Any]], label: str) -> str:
    suffix_parts = [f"({n['label'].strip('()')})" for n in stack[1:]]  # skip section root
    suffix_parts.append(f"({label})")
    return base_id + "".join(suffix_parts)


def new_node(
    base_id: str,
    stack: List[Dict[str, Any]],
    level: str,
    label: str,
    heading_text: Optional[str],
    body_text: str,
) -> Dict[str, Any]:
    node = {
        "id": make_node_id(base_id, stack, label),
        "label": f"({label})",
        "level": level,
        "heading": heading_text,
        "text": body_text.strip(),
        "children": [],
    }
    return node


def extract_direct_text(element: Optional[ET.Element]) -> str:
    if element is None:
        return ""

    parts: List[str] = []

    if element.text and element.text.strip():
        parts.append(element.text.strip())

    for child in list(element):
        name = local_name(child.tag)
        if name in USLM_LEVEL_MAP:
            if child.tail and child.tail.strip():
                parts.append(child.tail.strip())
            continue
        nested = extract_direct_text(child)
        if nested:
            parts.append(nested)
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())

    return normalize(" ".join(parts)) if parts else ""


def build_uslm_node(
    element: ET.Element,
    base_id: str,
    stack: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    name = local_name(element.tag)
    level = USLM_LEVEL_MAP.get(name)
    if not level:
        return None

    raw_label = element.findtext("uslm:num", default="", namespaces=USLM_NS).strip()
    label_normalized = raw_label.rstrip(".")
    if label_normalized and not label_normalized.startswith("("):
        label_normalized = f"({label_normalized})"
    label_value = label_normalized.strip("()")
    if not label_value:
        return None

    heading_text = element.findtext("uslm:heading", default=None, namespaces=USLM_NS)
    if heading_text is not None:
        heading_text = heading_text.strip() or None

    content = element.find("uslm:content", namespaces=USLM_NS)
    body_text = extract_direct_text(content)

    node = new_node(base_id, stack, level, label_value, heading_text, body_text)

    children_parent = content if content is not None else element
    for child in list(children_parent):
        child_name = local_name(child.tag)
        if child_name in USLM_LEVEL_MAP:
            built = build_uslm_node(child, base_id, stack + [node])
            if built:
                node["children"].append(built)

    return node


def parse_uslm(source_text: str, source_type: str, citation_hint: str) -> ParseResult:
    try:
        root = ET.fromstring(source_text)
    except ET.ParseError:
        return ParseResult(
            document={
                "id": citation_hint or "unknown",
                "type": source_type.lower(),
                "source": {"work": source_type.upper(), "title": "", "section": ""},
                "nodes": [],
            },
            needs_llm_assist=True,
        )

    section = root
    if local_name(section.tag) != "section":
        section = root.find(".//uslm:section", namespaces=USLM_NS)
        if section is None:
            return ParseResult(
                document={
                    "id": citation_hint or "unknown",
                    "type": source_type.lower(),
                    "source": {"work": source_type.upper(), "title": "", "section": ""},
                    "nodes": [],
                },
                needs_llm_assist=True,
            )

    base_id, doc_type, source, heading_hint = metadata_from_hint(citation_hint, source_type)

    heading_text = section.findtext("uslm:heading", default=None, namespaces=USLM_NS)
    if heading_text is not None:
        heading_text = heading_text.strip() or None
    if not heading_text:
        heading_text = heading_hint

    section_node = {
        "id": base_id,
        "label": "",
        "level": "section",
        "heading": heading_text,
        "text": extract_direct_text(section.find("uslm:content", namespaces=USLM_NS)),
        "children": [],
    }

    content = section.find("uslm:content", namespaces=USLM_NS)
    if content is not None:
        for child in list(content):
            child_built = build_uslm_node(child, base_id, [section_node])
            if child_built:
                section_node["children"].append(child_built)

    document: Dict[str, Any] = {
        "id": base_id,
        "type": doc_type,
        "source": source,
        "heading": heading_text,
        "nodes": [section_node],
        "links": [],
        "spans": [],
        "meta": {},
    }

    return ParseResult(document=document, needs_llm_assist=False)


def parse(source_text: str, source_type: str, citation_hint: str = "", input_format: str = "text") -> ParseResult:
    if input_format.lower() == "uslm":
        return parse_uslm(source_text, source_type, citation_hint)

    text = normalize(source_text)

    m_cfr = HEADER_CFR.search(text) if source_type.upper() == "CFR" else None
    m_usc = HEADER_USC.search(text) if source_type.upper() != "CFR" else None

    if m_cfr:
        title, section, heading_line = m_cfr.group(1), m_cfr.group(2), m_cfr.group(3).strip() or None
        base_id = f"cfr:{title}:{section}"
        doc_type = "regulation"
        source = {"work": "CFR", "title": int(title), "section": section}
        heading = heading_line
    elif m_usc:
        title, section, heading_line = m_usc.group(1), m_usc.group(3), m_usc.group(4).strip() or None
        base_id = f"usc:{title}:{section}"
        doc_type = "statute"
        source = {"work": "USC", "title": int(title), "section": section}
        heading = heading_line
    else:
        # insufficient header info: request LLM assist
        return ParseResult(
            document={
                "id": citation_hint or "unknown",
                "type": source_type.lower(),
                "source": {"work": source_type.upper(), "title": citation_hint or "", "section": ""},
                "nodes": [],
            },
            needs_llm_assist=True,
        )

    lines = [ln for ln in text.split("\n") if ln.strip()]

    # Build root section node
    section_node = {
        "id": base_id,
        "label": "",
        "level": "section",
        "heading": heading,
        "text": "",
        "children": [],
    }
    stack: List[Dict[str, Any]] = [section_node]

    header_line = m_cfr.group(0) if m_cfr else m_usc.group(0)

    for line in lines:
        if line.strip() == header_line.strip():
            continue
        level, label, pos = detect_label(line)
        if level and label:
            level_idx = LEVEL_ORDER.index(level)
            while len(stack) > 1 and LEVEL_ORDER.index(stack[-1]["level"]) >= level_idx:
                stack.pop()
            parent = stack[-1]
            remainder = line[pos:].strip()
            heading_text, body_text = heading_and_body(remainder)
            node = new_node(base_id, stack, level, label, heading_text, body_text)
            if not node["text"]:
                node["text"] = remainder
            parent["children"].append(node)
            stack.append(node)
        else:
            # Append to the current node's text.
            current = stack[-1]
            addition = line.strip()
            if addition:
                if current["text"]:
                    current["text"] += " " + addition
                else:
                    current["text"] = addition

    document: Dict[str, Any] = {
        "id": base_id,
        "type": doc_type,
        "source": source,
        "heading": heading,
        "nodes": [section_node],
        "links": [],
        "spans": [],
        "meta": {},
    }

    return ParseResult(document=document, needs_llm_assist=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse statute or regulation text into JSON")
    parser.add_argument("input", help="Path to a text file or '-' for stdin")
    parser.add_argument("--source-type", choices=["USC", "CFR"], required=True)
    parser.add_argument("--citation-hint", default="")
    parser.add_argument(
        "--input-format",
        choices=["text", "uslm"],
        default="text",
        help="Treat the input as raw text or USLM XML",
    )
    args = parser.parse_args()

    if args.input == "-":
        source_text = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            source_text = f.read()

    result = parse(
        source_text,
        args.source_type,
        citation_hint=args.citation_hint,
        input_format=args.input_format,
    )
    output = {
        "json": result.document,
        "needs_llm_assist": result.needs_llm_assist,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
