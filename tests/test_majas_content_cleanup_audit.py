from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.audit_majas_content_cleanup import (
    NO_CANDIDATES_DECISION,
    READY_DECISION,
    analyze_content_cleanup,
    build_live_report,
    main,
)
from tools.materialize_majas_dashboard_candidate import (
    dump_yaml,
    load_candidate_tree,
    load_mapping,
)


def custom_card(
    index: int,
    *,
    duplicate_first_pair: bool = True,
    unique_shape: bool = False,
) -> dict[str, object]:
    entity_index = 0 if duplicate_first_pair and index == 1 else index
    card: dict[str, object] = {
        "type": "custom:neutral-card",
        "entity": f"sensor.example_{entity_index}",
        "name": f"Card {entity_index}",
    }
    if unique_shape:
        card[f"variant_{index}"] = True
    return card


def section_payloads(
    *,
    duplicate_first_pair: bool = True,
    unique_shape: bool = False,
) -> list[dict[str, object]]:
    cards = [
        custom_card(
            index,
            duplicate_first_pair=duplicate_first_pair,
            unique_shape=unique_shape,
        )
        for index in range(11)
    ]
    return [
        {"type": "grid", "cards": cards[:1]},
        {"type": "grid", "cards": cards[1:6]},
        {"type": "grid", "cards": cards[6:]},
    ]


class MajasContentCleanupAuditTests(unittest.TestCase):
    def build_fixture(
        self,
        root: Path,
        *,
        duplicate_first_pair: bool = True,
        unique_shape: bool = False,
    ) -> tuple[Path, Path]:
        config_root = root / "config"
        active_root = config_root / "majas_modular"
        (active_root / "views").mkdir(parents=True)
        (active_root / "sections" / "view_00").mkdir(parents=True)

        (config_root / "configuration.yaml").write_text(
            """lovelace:
  mode: yaml
  dashboards:
    majas-yaml:
      mode: yaml
      title: Mājas YAML
      show_in_sidebar: true
      filename: majas_modular/dashboard.yaml
""",
            encoding="utf-8",
        )
        (active_root / "dashboard.yaml").write_text(
            "views: !include_dir_list views\n",
            encoding="utf-8",
        )
        (active_root / "views" / "00_view.yaml").write_text(
            """type: sections
max_columns: 1
sections: !include_dir_list ../sections/view_00
""",
            encoding="utf-8",
        )

        for filename, payload in zip(
            ("00_section.yaml", "10_section.yaml", "20_section.yaml"),
            section_payloads(
                duplicate_first_pair=duplicate_first_pair,
                unique_shape=unique_shape,
            ),
            strict=True,
        ):
            (active_root / "sections" / "view_00" / filename).write_text(
                dump_yaml(payload),
                encoding="utf-8",
            )

        return config_root, active_root

    def test_analysis_finds_only_sanitized_cleanup_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, active_root = self.build_fixture(Path(tmp))
            payload = load_candidate_tree(active_root)

            report = analyze_content_cleanup(payload)

            self.assertEqual(report["decision"], READY_DECISION)
            self.assertEqual(
                report["reasons"],
                [
                    "EXACT_DUPLICATE_CARD_PAYLOAD_CANDIDATES",
                    "REPEATED_CARD_STRUCTURE_CANDIDATES",
                ],
            )
            exact = report["candidates"]["exact_duplicate_cards"]
            self.assertEqual(exact["group_count"], 1)
            self.assertEqual(exact["member_count"], 2)
            self.assertEqual(exact["largest_group_size"], 2)
            self.assertEqual(
                exact["groups"],
                [
                    [
                        {
                            "view_ordinal": 0,
                            "section_ordinal": 0,
                            "card_ordinal": 0,
                        },
                        {
                            "view_ordinal": 0,
                            "section_ordinal": 1,
                            "card_ordinal": 0,
                        },
                    ]
                ],
            )

            shapes = report["candidates"]["repeated_card_structures"]
            self.assertEqual(shapes["group_count"], 1)
            self.assertEqual(shapes["member_count"], 11)
            self.assertEqual(shapes["largest_group_size"], 11)

            references = report["references"]
            self.assertEqual(references["entity_like_occurrence_count"], 11)
            self.assertEqual(references["unique_entity_like_reference_count"], 10)
            self.assertEqual(references["repeated_entity_like_reference_count"], 1)
            self.assertEqual(references["largest_reference_occurrence_count"], 2)
            self.assertFalse(references["unused_entity_or_helper_claimed"])
            self.assertFalse(
                report["candidates"]["automatic_dedup_safe_claimed"]
            )
            self.assertFalse(
                report["candidates"]["automatic_removal_safe_claimed"]
            )

            rendered = str(report)
            self.assertNotIn("sensor.example_", rendered)
            self.assertNotIn("custom:neutral-card", rendered)
            self.assertNotIn(str(config_root), rendered)

    def test_no_candidate_decision_when_every_card_shape_is_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            _config_root, active_root = self.build_fixture(
                Path(tmp),
                duplicate_first_pair=False,
                unique_shape=True,
            )
            payload = load_candidate_tree(active_root)

            report = analyze_content_cleanup(payload)

            self.assertEqual(report["decision"], NO_CANDIDATES_DECISION)
            self.assertEqual(report["reasons"], [])
            self.assertEqual(
                report["candidates"]["exact_duplicate_cards"]["group_count"],
                0,
            )
            self.assertEqual(
                report["candidates"]["repeated_card_structures"]["group_count"],
                0,
            )

    def test_live_report_is_read_only_private_safe_and_exact_tree_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(Path(tmp))

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], READY_DECISION)
            self.assertTrue(report["home_assistant"]["version_match"])
            self.assertTrue(report["binding"]["resolved"])
            self.assertTrue(report["active_tree"]["exact"])
            self.assertEqual(report["active_tree"]["regular_files"], 5)
            self.assertEqual(report["active_tree"]["directories"], 3)
            self.assertEqual(report["active_tree"]["symlinks"], 0)
            self.assertEqual(report["active_tree"]["unexpected_entries"], 0)
            self.assertEqual(
                report["dashboard"]["guard"]["top_level_card_count"],
                11,
            )
            self.assertEqual(
                report["dashboard"]["guard"]["grouping_wrapper_count"],
                0,
            )
            self.assertTrue(all(value is False for value in report["privacy"].values()))
            self.assertTrue(all(value is False for value in report["mutation"].values()))

            rendered = str(report)
            self.assertNotIn("sensor.example_", rendered)
            self.assertNotIn("custom:neutral-card", rendered)
            self.assertNotIn(str(config_root), rendered)

    def test_active_tree_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, active_root = self.build_fixture(Path(tmp))
            (active_root / "unexpected.yaml").write_text(
                "value: true\n",
                encoding="utf-8",
            )

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["ACTIVE_TREE_MISMATCH"])

    def test_post_phase3_structure_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, active_root = self.build_fixture(Path(tmp))
            target = active_root / "sections" / "view_00" / "20_section.yaml"
            payload = load_mapping(target)
            cards = payload["cards"]
            cards.pop()
            target.write_text(dump_yaml(payload), encoding="utf-8")

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(
                report["reasons"],
                ["POST_PHASE3_STRUCTURE_MISMATCH"],
            )

    def test_version_mismatch_blocks_before_private_resolution(self):
        report = build_live_report(
            config_root=Path("/definitely/not/private/config"),
            dashboard_title="private",
            expected_version="2026.8.2",
            running_version="2026.8.1",
        )

        self.assertEqual(report["decision"], "BLOCKED")
        self.assertEqual(
            report["reasons"],
            ["HOME_ASSISTANT_VERSION_MISMATCH"],
        )

    def test_cli_without_audit_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        rendered = stdout.getvalue()
        self.assertIn('"AUDIT_GATE_REQUIRED"', rendered)
        self.assertIn('"dashboard_modified": false', rendered)
        self.assertIn('"card_or_helper_removed": false', rendered)
        self.assertIn('"reload_or_restart": false', rendered)


if __name__ == "__main__":
    unittest.main()
