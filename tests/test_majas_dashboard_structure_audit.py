import json
import unittest

from tools.audit_majas_dashboard_structure import build_report


EXPECTED = "2026.8.2"
SHA = "a" * 40


def ready_probe() -> dict:
    return {
        "resolved": True,
        "reason": "",
        "structure": {
            "line_count": 2400,
            "view_count": 7,
            "section_count": 4,
            "card_count": 180,
            "horizontal_stack_card_count": 18,
            "vertical_stack_card_count": 11,
            "grid_card_count": 7,
            "conditional_card_count": 9,
            "custom_card_count": 30,
            "distinct_custom_card_type_count": 5,
            "include_directive_count": 0,
            "largest_view_card_count": 55,
            "largest_view_complexity": "very-large",
            "largest_section_card_count": 12,
            "largest_section_complexity": "medium",
            "views": [
                {
                    "view": "view_00",
                    "cards": 55,
                    "sections": 2,
                    "stack_cards": 10,
                    "custom_cards": 8,
                    "complexity": "very-large",
                },
                {
                    "view": "view_01",
                    "cards": 20,
                    "sections": 2,
                    "stack_cards": 3,
                    "custom_cards": 4,
                    "complexity": "medium",
                },
            ],
        },
        "privacy": {
            "dashboard_path_emitted": False,
            "raw_yaml_emitted": False,
            "entity_ids_emitted": False,
            "view_names_or_paths_emitted": False,
            "card_titles_emitted": False,
            "custom_card_type_names_emitted": False,
            "secrets_resolved": False,
        },
    }


class DashboardStructureAuditTests(unittest.TestCase):
    def test_ready_report_contains_only_sanitized_structure(self) -> None:
        report = build_report(
            sha=SHA,
            expected=EXPECTED,
            running=EXPECTED,
            probe=ready_probe(),
        )

        self.assertEqual(report["decision"], "READY_FOR_SPLIT_DESIGN")
        self.assertEqual(report["reasons"], [])
        self.assertTrue(report["dashboard"]["resolved"])
        self.assertEqual(
            report["dashboard"]["structure"]["views"][0]["view"],
            "view_00",
        )
        self.assertEqual(
            report["mutation"],
            {
                "home_assistant_write": False,
                "dashboard_write": False,
                "storage_write": False,
                "reload_or_restart": False,
            },
        )

    def test_version_mismatch_blocks(self) -> None:
        report = build_report(
            sha=SHA,
            expected=EXPECTED,
            running="2026.8.1",
            probe=ready_probe(),
        )

        self.assertEqual(report["decision"], "BLOCKED")
        self.assertIn("HOME_ASSISTANT_VERSION_MISMATCH", report["reasons"])

    def test_unresolved_dashboard_blocks(self) -> None:
        probe = ready_probe()
        probe["resolved"] = False
        probe["reason"] = "DASHBOARD_BINDING_NOT_UNIQUE"
        probe.pop("structure")

        report = build_report(
            sha=SHA,
            expected=EXPECTED,
            running=EXPECTED,
            probe=probe,
        )

        self.assertEqual(report["decision"], "BLOCKED")
        self.assertIn("DASHBOARD_BINDING_NOT_UNIQUE", report["reasons"])

    def test_unparsable_structure_requires_review(self) -> None:
        probe = ready_probe()
        probe["structure"] = {}
        probe["reason"] = "DASHBOARD_STRUCTURE_UNPARSABLE"

        report = build_report(
            sha=SHA,
            expected=EXPECTED,
            running=EXPECTED,
            probe=probe,
        )

        self.assertEqual(report["decision"], "NEEDS_REVIEW")
        self.assertIn("DASHBOARD_STRUCTURE_UNPARSABLE", report["reasons"])

    def test_privacy_guard_failure_blocks(self) -> None:
        probe = ready_probe()
        probe["privacy"]["entity_ids_emitted"] = True

        report = build_report(
            sha=SHA,
            expected=EXPECTED,
            running=EXPECTED,
            probe=probe,
        )

        self.assertEqual(report["decision"], "BLOCKED")
        self.assertIn("PRIVACY_GUARD_FAILED", report["reasons"])

    def test_public_report_has_no_private_value_fields(self) -> None:
        report = build_report(
            sha=SHA,
            expected=EXPECTED,
            running=EXPECTED,
            probe=ready_probe(),
        )
        rendered = json.dumps(report, sort_keys=True)

        for forbidden in (
            "entity_id",
            "dashboard_path",
            "card_title",
            "custom_type_name",
            "raw_yaml",
            "secret_value",
        ):
            self.assertNotIn(forbidden + ":", rendered)


if __name__ == "__main__":
    unittest.main()
