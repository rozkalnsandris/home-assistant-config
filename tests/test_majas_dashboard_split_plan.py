import unittest

from tools.plan_majas_dashboard_split import build_report, reassemble, split_in_memory


def custom_card(name: str) -> dict:
    return {"type": "custom:fixture-card", "entity": f"sensor.{name}"}


def fixture() -> dict:
    return {
        "title": "Private fixture",
        "background": "local/example.png",
        "views": [
            {
                "title": "Fixture view",
                "path": "fixture",
                "type": "sections",
                "sections": [
                    {
                        "type": "grid",
                        "cards": [
                            custom_card("a"),
                            custom_card("b"),
                            custom_card("c"),
                            custom_card("d"),
                            custom_card("e"),
                            custom_card("f"),
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            custom_card("g"),
                            custom_card("h"),
                            custom_card("i"),
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            custom_card("j"),
                            custom_card("k"),
                            {"type": "grid", "cards": []},
                        ],
                    },
                ],
            }
        ],
    }


class MajasDashboardSplitPlanTests(unittest.TestCase):
    def test_split_round_trip_preserves_entire_payload(self) -> None:
        payload = fixture()
        self.assertEqual(reassemble(split_in_memory(payload)), payload)

    def test_report_preserves_reviewed_counts(self) -> None:
        report = build_report(fixture())
        self.assertEqual(report["decision"], "READY_FOR_PRIVATE_CANDIDATE_GATE")
        self.assertEqual(report["structure"]["before"]["view_count"], 1)
        self.assertEqual(report["structure"]["before"]["section_count"], 3)
        self.assertEqual(report["structure"]["before"]["card_count"], 12)
        self.assertEqual(report["structure"]["before"]["custom_card_count"], 11)
        self.assertEqual(
            report["structure"]["before"]["distinct_custom_card_type_count"], 1
        )
        self.assertEqual(report["structure"]["before"], report["structure"]["after"])

    def test_section_order_and_payloads_are_preserved(self) -> None:
        payload = fixture()
        model = split_in_memory(payload)
        self.assertEqual(model["sections"], payload["views"][0]["sections"])
        report = build_report(payload)
        self.assertTrue(
            report["equivalence"]["section_payloads_and_order_preserved"]
        )
        self.assertEqual(report["structure"]["ordered_section_count"], 3)

    def test_dashboard_and_view_fields_are_preserved(self) -> None:
        report = build_report(fixture())
        self.assertTrue(report["equivalence"]["dashboard_level_preserved"])
        self.assertTrue(report["equivalence"]["view_non_section_fields_preserved"])
        self.assertTrue(report["equivalence"]["whole_structure_equivalent"])

    def test_report_is_private_safe_and_non_mutating(self) -> None:
        report = build_report(fixture())
        rendered = str(report)
        self.assertNotIn("sensor.a", rendered)
        self.assertNotIn("Fixture view", rendered)
        self.assertFalse(report["privacy"]["raw_private_values_emitted"])
        self.assertFalse(report["mutation"]["filesystem_write"])
        self.assertFalse(report["mutation"]["live_dashboard_binding_changed"])
        self.assertFalse(report["mutation"]["reload_or_restart"])

    def test_wrong_shape_fails_closed(self) -> None:
        payload = fixture()
        payload["views"][0]["sections"].pop()
        report = build_report(payload)
        self.assertEqual(report["decision"], "BLOCKED")
        self.assertIn("BASELINE_STRUCTURE_MISMATCH", report["reasons"])


if __name__ == "__main__":
    unittest.main()
