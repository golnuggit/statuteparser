import json
from pathlib import Path
import unittest

from statute_to_json.tools.parse_statute import parse
from statute_to_json.tools.resolve_links import resolve


class ParsePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_text = Path("statute_to_json/examples/input_26usc_162.txt").read_text()
        self.expected_output = json.loads(Path("statute_to_json/examples/output_26usc_162.json").read_text())

    def test_parse_generates_section_root(self):
        result = parse(self.sample_text, "USC")
        self.assertFalse(result.needs_llm_assist)
        document = result.document
        self.assertEqual(document["id"], "usc:26:162")
        self.assertEqual(document["nodes"][0]["level"], "section")
        children_labels = [child["label"] for child in document["nodes"][0]["children"]]
        self.assertIn("(a)", children_labels)
        self.assertIn("(b)", children_labels)

    def test_resolve_links_finds_relative_and_absolute(self):
        resolved = resolve(parse(self.sample_text, "USC").document)
        link_relations = {link["relation"] for link in resolved["links"]}
        self.assertIn("subject-to", link_relations)
        self.assertIn("for-purposes-of", link_relations)
        self.assertIn("exception-to", link_relations)
        targets = {link["target"] for link in resolved["links"]}
        self.assertIn("usc:26:162(a)(1)", targets)
        self.assertIn("usc:26:274", targets)

        section = resolved["nodes"][0]
        subsection_b = next(child for child in section["children"] if child["label"] == "(b)")
        self.assertIn("[[usc:26:162(a)]]", subsection_b["text"])
        paragraph_one = next(child for child in subsection_b["children"] if child["label"] == "(1)")
        self.assertIn("[[usc:26:274]]", paragraph_one["text"])

    def test_examples_are_consistent_with_pipeline(self):
        pipeline_output = resolve(parse(self.sample_text, "USC").document)
        self.assertEqual(pipeline_output["id"], self.expected_output["id"])
        self.assertEqual(pipeline_output["links"], self.expected_output["links"])

    def test_double_letter_labels_map_to_items(self):
        complex_text = (
            "26 U.S.C. § 999 Sample.\n"
            "(a) Subsection.\n"
            "(1) Paragraph.\n"
            "(A) Subparagraph.\n"
            "(i) Clause.\n"
            "(aa) Item text.\n"
            "(AA) Subitem text.\n"
        )
        parsed = parse(complex_text, "USC").document
        section = parsed["nodes"][0]

        def find_by_label(node_list, label):
            for node in node_list:
                if node.get("label") == label:
                    return node
                found = find_by_label(node.get("children", []), label)
                if found:
                    return found
            return None

        item = find_by_label(section["children"], "(aa)")
        subitem = find_by_label(section["children"], "(AA)")
        self.assertIsNotNone(item)
        self.assertEqual(item["level"], "item")
        self.assertIsNotNone(subitem)
        self.assertEqual(subitem["level"], "subitem")

    def test_parse_uslm_input(self):
        uslm_snippet = (
            "<uslm:section xmlns:uslm=\"http://xml.house.gov/schemas/uslm/1.0\">"
            "<uslm:num>Sec. 999.</uslm:num>"
            "<uslm:heading>Sample heading</uslm:heading>"
            "<uslm:content>"
            "<uslm:subsection>"
            "<uslm:num>(a)</uslm:num>"
            "<uslm:heading>In general</uslm:heading>"
            "<uslm:content>"
            "<uslm:paragraph>"
            "<uslm:num>(1)</uslm:num>"
            "<uslm:content>Ordinary expenses.</uslm:content>"
            "</uslm:paragraph>"
            "</uslm:content>"
            "</uslm:subsection>"
            "</uslm:content>"
            "</uslm:section>"
        )

        result = parse(uslm_snippet, "USC", citation_hint="26 U.S.C. § 999", input_format="uslm")
        self.assertFalse(result.needs_llm_assist)
        document = result.document
        self.assertEqual(document["id"], "usc:26:999")
        section = document["nodes"][0]
        self.assertEqual(section["heading"], "Sample heading")
        subsection = section["children"][0]
        self.assertEqual(subsection["label"], "(a)")
        paragraph = subsection["children"][0]
        self.assertEqual(paragraph["text"], "Ordinary expenses.")


if __name__ == "__main__":
    unittest.main()
