from __future__ import annotations

import unittest

from tools.plan_majas_2026_9_modernization import (
    Majas20269PlanError,
    build_private_plan,
    finalize_private_plan,
)


def current_payload():
    cards = [
        {"type": "custom:synthetic", "entity": f"sensor.synthetic_{index}"}
        for index in range(11)
    ]
    for index, card in enumerate(cards):
        card["template"] = "one" if index < 6 else "two"
    cards[0]["tap_action"] = {
        "action": "call-service",
        "service": "light.turn_on",
        "service_data": {"brightness": 100},
        "target": {"entity_id": "light.synthetic"},
    }
    cards[1]["tap_action"] = {
        "action": "fire-dom-event",
        "browser_mod": {"service": "browser_mod.popup", "data": {"title": "private"}},
    }
    return {
        "button_card_templates": {
            "one": {
                "triggers_update": ["sensor.synthetic"],
                "styles": {"card": [{"height": "64px"}, {"border-radius": "12px"}]},
            },
            "two": {"styles": {"card": [{"width": "100%"}]}},
        },
        "views": [
            {
                "type": "sections",
                "sections": [
                    {"type": "grid", "cards": cards[:4]},
                    {"type": "grid", "cards": cards[4:8]},
                    {"type": "grid", "cards": cards[8:]},
                ],
            }
        ],
    }


def lovelace_mapping():
    return {
        "mode": "storage",
        "dashboards": {"synthetic": {"mode": "yaml", "filename": "private.yaml"}},
    }


class Majas20269ModernizationPlanTests(unittest.TestCase):
    def test_builds_bounded_private_candidate_without_guessing_grid_options(self):
        plan = build_private_plan(
            current_payload(),
            lovelace_mapping(),
            custom_card_sections_capability="proven",
            triggers_update_runtime="absent",
        )

        delta = plan.report["planned_delta"]
        self.assertEqual(delta["top_level_section_mode_changes"], 11)
        self.assertEqual(delta["root_dimension_declarations_removed"], 2)
        self.assertEqual(delta["triggers_update_declarations_removed"], 1)
        self.assertEqual(delta["legacy_service_actions_modernized"], 1)
        self.assertEqual(delta["legacy_lovelace_mode_entries_removed"], 1)
        self.assertEqual(delta["grid_options_added"], 0)
        for section in plan.dashboard["views"][0]["sections"]:
            for card in section["cards"]:
                self.assertIs(card["section_mode"], True)
                self.assertNotIn("grid_options", card)

    def test_action_conversion_preserves_target_and_browser_event(self):
        before = current_payload()
        browser_before = before["views"][0]["sections"][0]["cards"][1]["tap_action"]
        plan = build_private_plan(
            before,
            lovelace_mapping(),
            custom_card_sections_capability="proven",
            triggers_update_runtime="absent",
        )
        action = plan.dashboard["views"][0]["sections"][0]["cards"][0]["tap_action"]
        browser_after = plan.dashboard["views"][0]["sections"][0]["cards"][1]["tap_action"]

        self.assertEqual(action["action"], "perform-action")
        self.assertEqual(action["perform_action"], "light.turn_on")
        self.assertEqual(action["data"], {"brightness": 100})
        self.assertEqual(action["target"], {"entity_id": "light.synthetic"})
        self.assertEqual(browser_after, browser_before)

    def test_report_is_privacy_safe(self):
        plan = build_private_plan(
            current_payload(),
            lovelace_mapping(),
            custom_card_sections_capability="proven",
            triggers_update_runtime="absent",
        )
        rendered = str(plan.report)

        self.assertNotIn("sensor.synthetic", rendered)
        self.assertNotIn("light.synthetic", rendered)
        self.assertNotIn("browser_mod.popup", rendered)
        self.assertNotIn("private.yaml", rendered)

    def test_capability_must_be_proven(self):
        with self.assertRaisesRegex(
            Majas20269PlanError, "AUDIT_NOT_READY_FOR_BOUNDED_PLAN"
        ):
            build_private_plan(
                current_payload(),
                lovelace_mapping(),
                custom_card_sections_capability="unknown",
                triggers_update_runtime="absent",
            )

    def test_ambiguous_service_data_fails_closed(self):
        payload = current_payload()
        action = payload["views"][0]["sections"][0]["cards"][0]["tap_action"]
        action["data"] = {"synthetic": True}

        with self.assertRaisesRegex(Majas20269PlanError, "LEGACY_ACTION_NOT_EXACT"):
            build_private_plan(
                payload,
                lovelace_mapping(),
                custom_card_sections_capability="proven",
                triggers_update_runtime="absent",
            )

    def test_final_ready_requires_private_candidate_validation(self):
        plan = build_private_plan(
            current_payload(),
            lovelace_mapping(),
            custom_card_sections_capability="proven",
            triggers_update_runtime="absent",
        )

        ready = finalize_private_plan(plan, candidate_validation_passed=True)
        blocked = finalize_private_plan(plan, candidate_validation_passed=False)

        self.assertEqual(ready["decision"], "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY")
        self.assertEqual(blocked["decision"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
