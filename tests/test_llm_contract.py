from __future__ import annotations

import unittest

from pldr_api.llm import model_request_payload, normalize_model_result


class ModelContractTest(unittest.TestCase):
    def test_request_names_the_exact_candidate_fields(self):
        request = model_request_payload(
            "extract_intake_candidates",
            {"snapshot": "The operator verified the report."},
        )

        contract = request["required_output"]
        self.assertEqual(set(contract), {"event", "entities", "claims"})
        self.assertIn("summary", contract["event"])
        self.assertIn("event_time", contract["event"])
        self.assertIn("location_name", contract["event"])
        self.assertIn("entity_type", contract["entities"][0])
        self.assertIn("text", contract["claims"][0])
        self.assertIn("snippet", contract["claims"][0]["evidence"][0])

    def test_common_provider_aliases_are_normalized_without_losing_source_text(self):
        raw = {
            "event": {
                "title": "Verification",
                "description": "A verification occurred.",
                "occurred_at": "2026-08-29",
                "location": {"name": "local host"},
            },
            "entities": [{"name": "SearXNG", "type": "software"}],
            "claims": [
                {
                    "claim": "The operator verified the report.",
                    "evidence": [
                        {
                            "quote": "The operator verified the report.",
                            "stance": "supports",
                            "strength": 0.8,
                        }
                    ],
                }
            ],
        }

        normalized = normalize_model_result("extract_intake_candidates", raw)

        self.assertEqual(normalized["event"]["summary"], "A verification occurred.")
        self.assertEqual(normalized["event"]["event_time"], "2026-08-29")
        self.assertEqual(normalized["event"]["location_name"], "local host")
        self.assertEqual(normalized["entities"][0]["entity_type"], "software")
        self.assertEqual(normalized["claims"][0]["text"], "The operator verified the report.")
        self.assertEqual(
            normalized["claims"][0]["evidence"][0]["snippet"],
            "The operator verified the report.",
        )
        self.assertNotIn("summary", raw["event"])
        self.assertNotIn("text", raw["claims"][0])

    def test_title_alias_is_normalized_and_non_objects_fail_closed(self):
        self.assertEqual(
            normalize_model_result("normalize_event_title", {"answer": "Normalized title"})["title"],
            "Normalized title",
        )
        with self.assertRaisesRegex(ValueError, "JSON object"):
            normalize_model_result("normalize_event_title", ["not", "an", "object"])


if __name__ == "__main__":
    unittest.main()
