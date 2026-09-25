from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools.audit_majas_dashboard_quality import (
    DashboardQualityAuditError,
    analyze_dashboard_quality,
    blocked_report,
    build_live_report,
    inventory_active_tree,
    main,
)
from tools.plan_majas_dashboard_activation import (
    EXPECTED_CANDIDATE_DIRS,
    EXPECTED_CANDIDATE_FILES,
)


def custom_card(
    *,
    grid_options=None,
    tap_action=None,
    hold_action=None,
    double_tap_action=None,
    section_mode=True,
):
    card = {
        "type": "custom:synthetic-card",
        "entity": "sensor.synthetic",
    }
    if section_mode is not None:
        card["section_mode"] = section_mode
    if grid_options is not None:
        card["grid_options"] = grid_options
    if tap_action is not None:
        card["tap_action"] = tap_action
    if hold_action is not None:
        card["hold_action"] = hold_action
    if double_tap_action is not None:
        card["double_tap_action"] = double_tap_action
    return card


def accepted_payload():
    cards = [custom_card() for _ in range(11)]
    return {
        "views": [
            {
                "type": "sections",
                "sections": [
                    {"type": "grid", "cards": cards[0:4]},
                    {"type": "grid", "cards": cards[4:8]},
                    {"type": "grid", "cards": cards[8:11]},
                ],
            }
        ]
    }


class DashboardQualityAuditTests(unittest.TestCase):
    def test_accepts_post_roadmap_11_card_baseline(self):
        report = analyze_dashboard_quality(accepted_payload())

        self.assertEqual(
            report["decision"], "DASHBOARD_2026_9_CURRENTLY_ALIGNED_NO_CHANGE"
        )
        self.assertEqual(report["structure"]["card_count"], 11)
        self.assertEqual(report["layout"]["top_level_card_count"], 11)
        self.assertEqual(report["layout"]["grouping_wrapper_count"], 0)

    def test_stale_12_card_historical_shape_is_not_current_truth(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"].append(custom_card())

        with self.assertRaisesRegex(
            DashboardQualityAuditError,
            "POST_ROADMAP_STRUCTURE_MISMATCH",
        ):
            analyze_dashboard_quality(payload)

    def test_invalid_sections_layout_fails_closed(self):
        payload = accepted_payload()
        payload["views"][0]["max_columns"] = 0

        with self.assertRaisesRegex(
            DashboardQualityAuditError,
            "LAYOUT_DECLARATION_INVALID",
        ):
            analyze_dashboard_quality(payload)

    def test_invalid_grid_options_fails_closed(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["grid_options"] = {
            "columns": 13
        }

        with self.assertRaisesRegex(
            DashboardQualityAuditError,
            "LAYOUT_DECLARATION_INVALID",
        ):
            analyze_dashboard_quality(payload)

    def test_grid_options_classifies_explicit_and_default(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["grid_options"] = {
            "columns": 6
        }

        report = analyze_dashboard_quality(payload)

        self.assertEqual(report["layout"]["grid_options"]["explicit_count"], 1)
        self.assertEqual(report["layout"]["grid_options"]["default_count"], 10)
        self.assertEqual(report["layout"]["width"]["bounded_count"], 1)
        self.assertEqual(report["layout"]["width"]["default_count"], 10)

    def test_custom_sizing_unknown_never_authorizes_rewrite(self):
        report = analyze_dashboard_quality(accepted_payload())

        custom = report["layout"]["custom_cards"]
        self.assertEqual(custom["runtime_sections_sizing_capability"], "unknown")
        self.assertFalse(custom["automatic_sizing_rewrite_authorized"])

    def test_native_replacement_unknown_never_authorizes_conversion(self):
        report = analyze_dashboard_quality(accepted_payload())

        replacement = report["layout"]["native_replacement"]
        self.assertEqual(replacement["eligible_count"], 0)
        self.assertEqual(replacement["unknown_count"], 11)
        self.assertFalse(replacement["automatic_conversion_authorized"])

    def test_action_classification_emits_no_private_scalar_values(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["tap_action"] = {
            "action": "perform-action",
            "perform_action": "lock.lock",
            "target": {"entity_id": "lock.synthetic_front_door"},
            "confirmation": {"text": "synthetic confirmation"},
        }
        payload["views"][0]["sections"][0]["cards"][1]["tap_action"] = {
            "action": "url",
            "url_path": "https://synthetic.invalid/path",
        }

        report = analyze_dashboard_quality(payload)
        rendered = str(report)

        self.assertEqual(
            report["actions"]["higher_impact_state_changing_count"], 1
        )
        self.assertEqual(report["actions"]["confirmation_coverage_count"], 1)
        self.assertNotIn("lock.synthetic_front_door", rendered)
        self.assertNotIn("synthetic confirmation", rendered)
        self.assertNotIn("https://synthetic.invalid/path", rendered)
        self.assertNotIn("lock.lock", rendered)

    def test_boolean_confirmation_true_counts_as_protection(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["tap_action"] = {
            "action": "perform-action",
            "perform_action": "lock.lock",
            "target": {"entity_id": "lock.synthetic"},
            "confirmation": True,
        }

        report = analyze_dashboard_quality(payload)

        self.assertEqual(
            report["decision"], "DASHBOARD_2026_9_CURRENTLY_ALIGNED_NO_CHANGE"
        )
        self.assertEqual(report["actions"]["confirmation_coverage_count"], 1)
        self.assertEqual(report["actions"]["unguarded_higher_impact_count"], 0)

    def test_boolean_confirmation_false_remains_unprotected(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["tap_action"] = {
            "action": "perform-action",
            "perform_action": "lock.lock",
            "target": {"entity_id": "lock.synthetic"},
            "confirmation": False,
        }

        report = analyze_dashboard_quality(payload)

        self.assertEqual(
            report["decision"], "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY"
        )
        self.assertEqual(report["actions"]["confirmation_coverage_count"], 0)
        self.assertEqual(report["actions"]["unguarded_higher_impact_count"], 1)

    def test_unguarded_higher_impact_action_is_bounded_candidate(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["tap_action"] = {
            "action": "perform-action",
            "perform_action": "lock.lock",
            "target": {"entity_id": "lock.synthetic"},
        }

        report = analyze_dashboard_quality(payload)

        self.assertEqual(
            report["decision"], "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY"
        )
        self.assertEqual(report["candidate_classes"], ["ACTION_SAFETY"])
        self.assertEqual(
            report["actions"]["unguarded_higher_impact_count"], 1
        )

    def test_missing_section_mode_is_detected_when_capability_proven(self):
        payload = accepted_payload()
        for section in payload["views"][0]["sections"]:
            for card in section["cards"]:
                card.pop("section_mode")

        report = analyze_dashboard_quality(
            payload, custom_card_sections_capability="proven"
        )

        section_mode = report["layout"]["custom_cards"]["section_mode"]
        self.assertEqual(section_mode["missing_count"], 11)
        self.assertIn("SECTIONS_SIZING", report["candidate_classes"])
        self.assertEqual(report["decision"], "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY")

    def test_missing_section_mode_needs_review_when_capability_unknown(self):
        payload = accepted_payload()
        for section in payload["views"][0]["sections"]:
            for card in section["cards"]:
                card.pop("section_mode")

        report = analyze_dashboard_quality(payload)

        self.assertEqual(report["decision"], "NEEDS_PRIVATE_REVIEW")
        self.assertIn(
            "CUSTOM_CARD_SECTIONS_CAPABILITY_NOT_PROVEN", report["reasons"]
        )

    def test_fixed_root_dimensions_and_dead_triggers_are_classified(self):
        payload = accepted_payload()
        for section in payload["views"][0]["sections"]:
            for card in section["cards"]:
                card["template"] = "synthetic"
        payload["button_card_templates"] = {
            "synthetic": {
                "triggers_update": ["sensor.synthetic"],
                "styles": {"card": [{"height": "64px"}, {"width": "100%"}]},
            }
        }

        report = analyze_dashboard_quality(
            payload, triggers_update_runtime="absent"
        )
        custom = report["layout"]["custom_cards"]

        self.assertEqual(custom["template_metrics"]["fixed_root_height_count"], 1)
        self.assertEqual(custom["template_metrics"]["fixed_root_width_count"], 1)
        self.assertEqual(custom["triggers_update_declaration_count"], 1)
        self.assertIn("DEAD_TRIGGERS_UPDATE", report["candidate_classes"])

    def test_invalid_section_mode_fails_closed(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["section_mode"] = "yes"

        with self.assertRaisesRegex(
            DashboardQualityAuditError, "SECTION_MODE_DECLARATION_INVALID"
        ):
            analyze_dashboard_quality(payload)

    def test_action_syntax_classifies_legacy_and_browser_event(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0]["tap_action"] = {
            "action": "call-service",
            "service": "light.turn_on",
            "target": {"entity_id": "light.synthetic"},
        }
        payload["views"][0]["sections"][0]["cards"][1]["tap_action"] = {
            "action": "fire-dom-event",
            "browser_mod": {"service": "browser_mod.popup"},
        }

        report = analyze_dashboard_quality(payload)
        syntax = report["actions"]["syntax_counts"]

        self.assertEqual(syntax["legacy_service_action"], 1)
        self.assertEqual(syntax["browser_or_integration_event"], 1)
        self.assertIn("ACTION_SYNTAX", report["candidate_classes"])
        self.assertNotIn("light.synthetic", str(report))
        self.assertNotIn("browser_mod.popup", str(report))

    def test_legacy_lovelace_mode_is_bounded_candidate(self):
        report = analyze_dashboard_quality(
            accepted_payload(), legacy_lovelace_mode_present=True
        )

        self.assertTrue(report["configuration"]["legacy_lovelace_mode_present"])
        self.assertIn("LOVELACE_LEGACY_MODE", report["candidate_classes"])

    def test_malformed_card_structure_fails_closed(self):
        payload = accepted_payload()
        payload["views"][0]["sections"][0]["cards"][0] = "not-a-card"

        with self.assertRaisesRegex(
            DashboardQualityAuditError,
            "POST_ROADMAP_STRUCTURE_MISMATCH",
        ):
            analyze_dashboard_quality(payload)

    def test_public_report_contains_no_private_values(self):
        payload = accepted_payload()
        payload["views"][0]["title"] = "Synthetic title"
        payload["views"][0]["sections"][0]["cards"][0]["tap_action"] = {
            "action": "navigate",
            "navigation_path": "/synthetic-path",
        }

        rendered = str(analyze_dashboard_quality(payload))

        self.assertNotIn("Synthetic title", rendered)
        self.assertNotIn("sensor.synthetic", rendered)
        self.assertNotIn("custom:synthetic-card", rendered)
        self.assertNotIn("/synthetic-path", rendered)

    def test_tree_inventory_requires_exact_modular_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for directory in EXPECTED_CANDIDATE_DIRS:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in EXPECTED_CANDIDATE_FILES:
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n", encoding="utf-8")

            report = inventory_active_tree(root)

        self.assertTrue(report["exact_expected_tree"])
        self.assertEqual(report["file_count"], 5)
        self.assertEqual(report["directory_count"], 3)
        self.assertEqual(report["symlink_count"], 0)
        self.assertEqual(report["unexpected_entry_count"], 0)

    def test_live_report_blocks_version_mismatch_before_private_resolution(self):
        report = build_live_report(
            config_root=mock.Mock(),
            dashboard_title="private",
            expected_version="2026.8.2",
            running_version="2026.8.1",
        )

        self.assertEqual(report, blocked_report("HOME_ASSISTANT_VERSION_MISMATCH"))

    def test_cli_without_audit_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        self.assertIn('"AUDIT_GATE_REQUIRED"', stdout.getvalue())
        self.assertIn('"dashboard_modified": false', stdout.getvalue())
        self.assertIn('"reload_or_restart": false', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
