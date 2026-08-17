from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tools.plan_majas_sections_modernization import (
    analyze_flattening_plan,
    blocked_report,
    build_live_plan,
    main,
)


def custom_card(index: int, *, grid_options=None):
    card = {
        "type": "custom:neutral-card",
        "entity": f"sensor.example_{index}",
    }
    if grid_options is not None:
        card["grid_options"] = grid_options
    return card


def bounded_payload(*, wrapper_title=None, extra_wrapper_key=None):
    wrapper = {
        "type": "grid",
        "columns": 2,
        "square": False,
        "cards": [
            custom_card(8),
            custom_card(9),
            custom_card(10),
            custom_card(11),
        ],
    }
    if wrapper_title is not None:
        wrapper["title"] = wrapper_title
    if extra_wrapper_key is not None:
        wrapper[extra_wrapper_key] = "unexpected"

    return {
        "views": [
            {
                "type": "sections",
                "max_columns": 1,
                "sections": [
                    {"type": "grid", "cards": [custom_card(0)]},
                    {
                        "type": "grid",
                        "cards": [
                            custom_card(1),
                            custom_card(2),
                            custom_card(3),
                            custom_card(4),
                            custom_card(5),
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            custom_card(6),
                            wrapper,
                        ],
                    },
                ],
            }
        ]
    }


class SectionsModernizationPlanTests(unittest.TestCase):
    def test_bounded_grid_wrapper_flattening_is_ready(self):
        report = analyze_flattening_plan(bounded_payload())

        self.assertEqual(
            report["decision"],
            "READY_FOR_PRIVATE_SECTIONS_FLATTENING_DRY_RUN",
        )
        self.assertEqual(report["structure"]["before"]["card_count"], 12)
        self.assertEqual(report["structure"]["proposed"]["card_count"], 11)
        self.assertEqual(report["structure"]["proposed"]["custom_card_count"], 11)
        self.assertEqual(report["structure"]["top_level_card_count_before"], 8)
        self.assertEqual(report["structure"]["top_level_card_count_proposed"], 11)
        self.assertEqual(report["structure"]["grouping_wrapper_count_before"], 1)
        self.assertEqual(report["structure"]["grouping_wrapper_count_proposed"], 0)
        self.assertTrue(report["plan"]["child_order_preserved"])
        self.assertTrue(report["plan"]["child_payloads_preserved"])
        self.assertTrue(report["plan"]["non_target_payloads_preserved"])
        self.assertFalse(report["plan"]["grid_options_change_planned"])
        self.assertTrue(report["plan"]["grid_options_count_preserved"])
        self.assertTrue(report["plan"]["visual_layout_change_expected"])
        self.assertTrue(report["plan"]["card_config_payload_preserved"])

    def test_title_bearing_wrapper_requires_private_review(self):
        report = analyze_flattening_plan(
            bounded_payload(wrapper_title="private heading")
        )

        self.assertEqual(report["decision"], "NEEDS_PRIVATE_REVIEW")
        self.assertEqual(report["reasons"], ["GRID_WRAPPER_TITLE_PRESENT"])
        self.assertTrue(report["plan"]["wrapper_title_present"])
        self.assertNotIn("private heading", str(report))

    def test_unsupported_grid_wrapper_key_blocks(self):
        with self.assertRaisesRegex(
            Exception,
            "GRID_WRAPPER_KEYS_UNSUPPORTED",
        ):
            analyze_flattening_plan(
                bounded_payload(extra_wrapper_key="layout_extension")
            )

    def test_non_sections_view_blocks(self):
        payload = bounded_payload()
        payload["views"][0]["type"] = "masonry"

        with self.assertRaisesRegex(Exception, "NATIVE_SECTIONS_REQUIRED"):
            analyze_flattening_plan(payload)

    def test_custom_sizing_evidence_drift_blocks(self):
        payload = bounded_payload()
        for section in payload["views"][0]["sections"]:
            for card in section["cards"]:
                if (
                    isinstance(card, dict)
                    and isinstance(card.get("type"), str)
                    and card["type"].startswith("custom:")
                ):
                    card["grid_options"] = {"columns": 6}

        with self.assertRaisesRegex(
            Exception,
            "CUSTOM_SIZING_EVIDENCE_DRIFT",
        ):
            analyze_flattening_plan(payload)

    def test_baseline_drift_blocks(self):
        payload = bounded_payload()
        payload["views"][0]["sections"][0]["cards"].pop()

        with self.assertRaisesRegex(Exception, "BASELINE_STRUCTURE_MISMATCH"):
            analyze_flattening_plan(payload)

    def test_live_plan_blocks_version_mismatch_before_private_resolution(self):
        report = build_live_plan(
            config_root=mock.Mock(),
            dashboard_title="private",
            expected_version="2026.8.2",
            running_version="2026.8.1",
        )

        self.assertEqual(
            report,
            blocked_report("HOME_ASSISTANT_VERSION_MISMATCH"),
        )

    def test_privacy_report_contains_no_private_values(self):
        report = analyze_flattening_plan(bounded_payload())
        rendered = str(report)

        self.assertNotIn("sensor.example_", rendered)
        self.assertNotIn("custom:neutral-card", rendered)
        self.assertNotIn("columns", rendered)
        self.assertNotIn("square", rendered)

    def test_cli_without_plan_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        self.assertIn('"PLAN_GATE_REQUIRED"', stdout.getvalue())
        self.assertIn('"dashboard_modified": false', stdout.getvalue())
        self.assertIn('"reload_or_restart": false', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
