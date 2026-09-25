from __future__ import annotations

import copy
import unittest

from tools.audit_majas_dashboard_quality import (
    DashboardQualityAuditError,
    analyze_dashboard_quality,
    classify_lovelace_mode,
)
from tools.plan_majas_dashboard_2026_9 import (
    ModernizationPlanError,
    plan_modernization,
)


def custom_card(template: str, *, section_mode=None):
    card = {
        "type": "custom:synthetic-card",
        "template": template,
        "entity": "sensor.synthetic_private",
    }
    if section_mode is not None:
        card["section_mode"] = section_mode
    return card


def accepted_payload(*, section_mode=None):
    cards = [
        custom_card("header", section_mode=section_mode),
        custom_card("heading", section_mode=section_mode),
        *[
            custom_card("climate", section_mode=section_mode)
            for _ in range(4)
        ],
        *[
            custom_card("device", section_mode=section_mode)
            for _ in range(5)
        ],
    ]
    templates = {
        "header": {"styles": {"card": [{"height": "80px"}]}},
        "heading": {"styles": {"card": [{"height": "32px"}]}},
        "climate": {
            "triggers_update": ["sensor.synthetic_private"],
            "tap_action": {
                "action": "call-service",
                "service": "input_select.select_option",
                "service_data": {"option": "synthetic"},
            },
            "hold_action": {
                "action": "fire-dom-event",
                "browser_mod": {
                    "service": "browser_mod.popup",
                    "data": {"title": "synthetic private title"},
                },
            },
            "styles": {"card": [{"height": "80px"}]},
        },
        "device": {"styles": {"card": [{"height": "80px"}]}},
    }
    return {
        "button_card_templates": templates,
        "views": [
            {
                "type": "sections",
                "sections": [
                    {"type": "grid", "cards": cards[0:4]},
                    {"type": "grid", "cards": cards[4:8]},
                    {"type": "grid", "cards": cards[8:11]},
                ],
            }
        ],
    }


def lovelace_mapping():
    return {
        "mode": "storage",
        "dashboards": {
            "synthetic-yaml": {
                "mode": "yaml",
                "title": "Synthetic",
                "filename": "synthetic/dashboard.yaml",
            }
        },
    }


class Dashboard20269AuditTests(unittest.TestCase):
    def test_missing_section_mode_and_fixed_height_are_detected(self):
        report = analyze_dashboard_quality(accepted_payload())

        modern = report["modernization"]
        self.assertEqual(modern["top_level_custom_card_count"], 11)
        self.assertEqual(modern["section_mode"]["missing_count"], 11)
        self.assertEqual(
            modern["fixed_root_dimensions"]["fixed_height_template_count"],
            4,
        )
        self.assertEqual(
            modern["fixed_root_dimensions"]["top_level_card_conflict_count"],
            11,
        )
        self.assertEqual(
            modern["dead_configuration"]["triggers_update_declaration_count"],
            1,
        )
        self.assertEqual(modern["action_schema"]["legacy_service"], 1)
        self.assertEqual(modern["action_schema"]["integration_event"], 1)

    def test_valid_section_mode_is_recognized(self):
        report = analyze_dashboard_quality(accepted_payload(section_mode=True))
        self.assertEqual(report["modernization"]["section_mode"]["true_count"], 11)
        self.assertEqual(report["modernization"]["section_mode"]["missing_count"], 0)

    def test_invalid_section_mode_fails_closed(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["section_mode"] = "yes"

        with self.assertRaisesRegex(
            DashboardQualityAuditError,
            "SECTION_MODE_DECLARATION_INVALID",
        ):
            analyze_dashboard_quality(payload)

    def test_lovelace_legacy_storage_mode_is_classified(self):
        report = classify_lovelace_mode(lovelace_mapping())
        self.assertTrue(report["legacy_mode_present"])
        self.assertEqual(report["legacy_mode_class"], "legacy_storage")


class Dashboard20269PlannerTests(unittest.TestCase):
    def test_planner_builds_bounded_private_candidate_without_guessing_grid(self):
        payload = accepted_payload()
        original = copy.deepcopy(payload)
        plan = plan_modernization(
            payload,
            lovelace_mapping(),
            sections_sizing_capability="proven",
            triggers_update_runtime="absent",
            candidate_validation_passed=False,
        )

        delta = plan.report["planned_delta"]
        self.assertEqual(plan.report["decision"], "NEEDS_PRIVATE_REVIEW")
        self.assertEqual(delta["section_mode_enabled_count"], 11)
        self.assertEqual(delta["fixed_dimension_entries_removed_count"], 4)
        self.assertEqual(delta["triggers_update_removed_count"], 1)
        self.assertEqual(delta["legacy_actions_converted_count"], 1)
        self.assertEqual(delta["legacy_lovelace_mode_removed_count"], 1)
        self.assertEqual(delta["grid_options_added_count"], 0)

        cards = []
        for section in plan.dashboard_candidate["views"][0]["sections"]:
            cards.extend(section["cards"])
        self.assertTrue(all(card["section_mode"] is True for card in cards))
        self.assertFalse(any("grid_options" in card for card in cards))
        self.assertNotIn(
            "triggers_update",
            plan.dashboard_candidate["button_card_templates"]["climate"],
        )

        climate = plan.dashboard_candidate["button_card_templates"]["climate"]
        self.assertEqual(climate["tap_action"]["action"], "perform-action")
        self.assertIn("perform_action", climate["tap_action"])
        self.assertNotIn("service", climate["tap_action"])
        self.assertEqual(climate["hold_action"]["action"], "fire-dom-event")
        self.assertNotIn("mode", plan.lovelace_candidate)
        self.assertEqual(payload, original)

    def test_validated_candidate_becomes_ready(self):
        plan = plan_modernization(
            accepted_payload(),
            lovelace_mapping(),
            sections_sizing_capability="proven",
            triggers_update_runtime="absent",
            candidate_validation_passed=True,
        )
        self.assertEqual(
            plan.report["decision"],
            "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY",
        )

    def test_unproven_sections_capability_fails_closed(self):
        with self.assertRaisesRegex(
            ModernizationPlanError,
            "SECTIONS_SIZING_CAPABILITY_UNPROVEN",
        ):
            plan_modernization(
                accepted_payload(),
                lovelace_mapping(),
                sections_sizing_capability="unknown",
                triggers_update_runtime="absent",
            )

    def test_reused_fixed_template_fails_closed(self):
        payload = accepted_payload()
        payload["button_card_templates"]["climate"]["nested"] = {
            "card": {
                "type": "custom:synthetic-card",
                "template": "device",
            }
        }

        with self.assertRaisesRegex(
            ModernizationPlanError,
            "FIXED_DIMENSION_TEMPLATE_REUSE_UNPROVEN",
        ):
            plan_modernization(
                payload,
                lovelace_mapping(),
                sections_sizing_capability="proven",
                triggers_update_runtime="absent",
            )

    def test_action_collision_fails_closed(self):
        payload = accepted_payload()
        payload["button_card_templates"]["climate"]["tap_action"][
            "perform_action"
        ] = "synthetic.collision"

        with self.assertRaisesRegex(
            ModernizationPlanError,
            "LEGACY_ACTION_COLLISION",
        ):
            plan_modernization(
                payload,
                lovelace_mapping(),
                sections_sizing_capability="proven",
                triggers_update_runtime="absent",
            )

    def test_public_report_does_not_emit_private_scalars(self):
        plan = plan_modernization(
            accepted_payload(),
            lovelace_mapping(),
            sections_sizing_capability="proven",
            triggers_update_runtime="absent",
        )
        rendered = str(plan.report)
        self.assertNotIn("sensor.synthetic_private", rendered)
        self.assertNotIn("synthetic private title", rendered)
        self.assertNotIn("input_select.select_option", rendered)
        self.assertNotIn("synthetic/dashboard.yaml", rendered)


if __name__ == "__main__":
    unittest.main()
