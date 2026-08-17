from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tools.audit_majas_sections_modernization import (
    analyze_sections_layout,
    blocked_report,
    build_live_report,
    main,
)


def custom_card(*, grid_options=None):
    card = {
        "type": "custom:neutral-card",
        "entity": "sensor.example",
    }
    if grid_options is not None:
        card["grid_options"] = grid_options
    return card


def modern_payload():
    cards = [custom_card(grid_options={"columns": 6}) for _ in range(11)]
    cards.append(
        {
            "type": "markdown",
            "content": "neutral",
            "grid_options": {"columns": "full", "rows": "auto"},
        }
    )
    return {
        "views": [
            {
                "type": "sections",
                "max_columns": 3,
                "dense_section_placement": False,
                "sections": [
                    {"type": "grid", "cards": cards[0:4]},
                    {"type": "grid", "cards": cards[4:8]},
                    {"type": "grid", "cards": cards[8:12]},
                ],
            }
        ]
    }


def grouping_payload():
    cards = [custom_card(grid_options={"columns": 6}) for _ in range(10)]
    grouping = {
        "type": "grid",
        "columns": 1,
        "square": False,
        "cards": [custom_card(grid_options={"columns": 6})],
    }
    return {
        "views": [
            {
                "type": "sections",
                "max_columns": 3,
                "dense_section_placement": False,
                "sections": [
                    {"type": "grid", "cards": cards[0:4]},
                    {"type": "grid", "cards": cards[4:8]},
                    {"type": "grid", "cards": cards[8:10] + [grouping]},
                ],
            }
        ]
    }


class SectionsModernizationAuditTests(unittest.TestCase):
    def test_modern_sections_can_report_no_action(self):
        report = analyze_sections_layout(modern_payload())

        self.assertEqual(report["decision"], "SECTIONS_ALREADY_MODERN_NO_ACTION")
        self.assertEqual(report["reasons"], [])
        self.assertTrue(report["layout"]["all_views_sections"])
        self.assertEqual(report["layout"]["top_level"]["card_count"], 12)
        self.assertEqual(
            report["layout"]["custom_cards"]["explicit_grid_options_count"],
            11,
        )
        self.assertFalse(
            report["layout"]["custom_cards"][
                "default_sizing_runtime_capability_unknown"
            ]
        )

    def test_grouping_grid_is_bounded_modernization_signal(self):
        report = analyze_sections_layout(grouping_payload())

        self.assertEqual(
            report["decision"],
            "READY_FOR_BOUNDED_SECTIONS_MODERNIZATION_DESIGN",
        )
        self.assertIn("GROUPING_LAYOUT_WRAPPER_PRESENT", report["reasons"])
        self.assertEqual(report["layout"]["top_level"]["grid_card_count"], 1)
        self.assertEqual(
            report["layout"]["grouping_wrappers"]["nested_card_count"],
            1,
        )
        self.assertEqual(
            report["layout"]["grouping_wrappers"]["nested_custom_card_count"],
            1,
        )

    def test_custom_default_sizing_is_review_not_automatic_change(self):
        payload = modern_payload()
        payload["views"][0]["sections"][0]["cards"][0].pop("grid_options")

        report = analyze_sections_layout(payload)

        self.assertEqual(report["decision"], "NEEDS_PRIVATE_REVIEW")
        self.assertIn("CUSTOM_CARD_DEFAULT_SIZING_UNCERTAIN", report["reasons"])
        self.assertEqual(
            report["layout"]["custom_cards"]["default_grid_options_count"],
            1,
        )

    def test_non_sections_view_is_modernization_signal(self):
        payload = modern_payload()
        payload["views"][0]["type"] = "masonry"

        report = analyze_sections_layout(payload)

        self.assertEqual(
            report["decision"],
            "READY_FOR_BOUNDED_SECTIONS_MODERNIZATION_DESIGN",
        )
        self.assertIn("NON_SECTIONS_VIEW_PRESENT", report["reasons"])

    def test_invalid_grid_options_needs_review(self):
        payload = modern_payload()
        payload["views"][0]["sections"][0]["cards"][0]["grid_options"] = "bad"

        report = analyze_sections_layout(payload)

        self.assertEqual(report["decision"], "NEEDS_PRIVATE_REVIEW")
        self.assertIn("LAYOUT_DECLARATION_NEEDS_REVIEW", report["reasons"])
        self.assertEqual(
            report["layout"]["top_level"]["invalid_grid_options_count"],
            1,
        )

    def test_baseline_drift_blocks(self):
        payload = modern_payload()
        payload["views"][0]["sections"][0]["cards"].pop()

        with self.assertRaisesRegex(Exception, "BASELINE_STRUCTURE_MISMATCH"):
            analyze_sections_layout(payload)

    def test_live_report_blocks_version_mismatch_before_private_resolution(self):
        report = build_live_report(
            config_root=mock.Mock(),
            dashboard_title="private",
            expected_version="2026.8.2",
            running_version="2026.8.1",
        )

        self.assertEqual(report, blocked_report("HOME_ASSISTANT_VERSION_MISMATCH"))

    def test_privacy_report_contains_no_private_values(self):
        rendered = str(analyze_sections_layout(grouping_payload()))

        self.assertNotIn("sensor.example", rendered)
        self.assertNotIn("custom:neutral-card", rendered)
        self.assertNotIn("markdown", rendered)

    def test_cli_without_audit_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        self.assertIn('"AUDIT_GATE_REQUIRED"', stdout.getvalue())
        self.assertIn('"reload_or_restart": false', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
