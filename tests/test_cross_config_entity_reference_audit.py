from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.audit_cross_config_entity_references import (
    NO_CANDIDATES_DECISION,
    READY_DECISION,
    build_live_report,
    main,
)
from tools.materialize_majas_dashboard_candidate import dump_yaml


class CrossConfigEntityReferenceAuditTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path]:
        config_root = root / "config"
        active_root = config_root / "majas_modular"
        (active_root / "views").mkdir(parents=True)
        (active_root / "sections" / "view_00").mkdir(parents=True)
        (config_root / ".storage").mkdir(parents=True)
        (config_root / "packages").mkdir()
        (config_root / "templates").mkdir()
        (config_root / "blueprints").mkdir()

        (config_root / "configuration.yaml").write_text(
            """lovelace:
  mode: yaml
  dashboards:
    majas-yaml:
      mode: yaml
      title: Mājas YAML
      show_in_sidebar: true
      filename: majas_modular/dashboard.yaml
example_reference: sensor.config_ref
another_reference: sensor.multi
""",
            encoding="utf-8",
        )
        (config_root / "automations.yaml").write_text(
            "- alias: Example\n  action:\n    - action: homeassistant.update_entity\n      target:\n        entity_id: sensor.auto_ref\n",
            encoding="utf-8",
        )
        (config_root / "scripts.yaml").write_text(
            "example:\n  sequence:\n    - variables:\n        watched: sensor.multi\n",
            encoding="utf-8",
        )
        (config_root / "scenes.yaml").write_text("[]\n", encoding="utf-8")
        (config_root / "packages" / "one.yaml").write_text(
            "example: sensor.package_ref\n",
            encoding="utf-8",
        )
        (config_root / "templates" / "one.yml").write_text(
            "example: sensor.template_ref\n",
            encoding="utf-8",
        )
        (config_root / "blueprints" / "one.yaml").write_text(
            "example: sensor.blueprint_ref\n",
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

        cards = [
            {
                "type": "custom:neutral-card",
                "entity": f"sensor.dash_{index}",
                "name": f"Card {index}",
            }
            for index in range(11)
        ]
        sections = [
            {"type": "grid", "cards": cards[:1]},
            {"type": "grid", "cards": cards[1:6]},
            {"type": "grid", "cards": cards[6:]},
        ]
        for filename, payload in zip(
            ("00_section.yaml", "10_section.yaml", "20_section.yaml"),
            sections,
            strict=True,
        ):
            (active_root / "sections" / "view_00" / filename).write_text(
                dump_yaml(payload),
                encoding="utf-8",
            )

        self.write_registry(
            config_root,
            [
                "sensor.dash_0",
                "sensor.config_ref",
                "sensor.auto_ref",
                "sensor.multi",
                "sensor.package_ref",
                "sensor.template_ref",
                "sensor.blueprint_ref",
                "sensor.unreferenced",
            ],
        )
        return config_root, active_root

    @staticmethod
    def write_registry(config_root: Path, entity_ids: list[str]) -> None:
        payload = {
            "version": 1,
            "minor_version": 1,
            "key": "core.entity_registry",
            "data": {
                "entities": [
                    {
                        "entity_id": entity_id,
                        "platform": "private-platform",
                        "unique_id": f"private-{index}",
                    }
                    for index, entity_id in enumerate(entity_ids)
                ]
            },
        }
        (config_root / ".storage" / "core.entity_registry").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def test_live_report_finds_only_unreferenced_in_corpus_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(Path(tmp))

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], READY_DECISION)
            self.assertEqual(
                report["reasons"],
                ["UNREFERENCED_IN_REVIEWED_CORPUS_PRESENT"],
            )
            self.assertTrue(report["home_assistant"]["version_match"])
            self.assertTrue(report["binding"]["resolved"])
            self.assertTrue(report["active_tree"]["exact"])
            self.assertEqual(report["active_tree"]["regular_files"], 5)
            self.assertEqual(report["active_tree"]["directories"], 3)
            self.assertEqual(report["dashboard"]["guard"]["top_level_card_count"], 11)
            self.assertEqual(report["dashboard"]["guard"]["grouping_wrapper_count"], 0)

            coverage = report["coverage"]
            self.assertEqual(coverage["registry_candidate_count"], 8)
            self.assertEqual(coverage["referenced_in_reviewed_corpus_count"], 7)
            self.assertEqual(coverage["unreferenced_in_reviewed_corpus_count"], 1)
            self.assertEqual(coverage["multiple_surface_reference_count"], 1)
            self.assertEqual(
                coverage["surface_reference_candidate_counts"],
                {
                    "automations": 1,
                    "blueprints": 1,
                    "configuration": 2,
                    "dashboard": 1,
                    "packages": 1,
                    "scenes": 0,
                    "scripts": 1,
                    "templates": 1,
                },
            )

            self.assertFalse(report["claims"]["unused_claimed"])
            self.assertFalse(report["claims"]["safe_to_remove_claimed"])
            self.assertFalse(report["claims"]["automatic_removal_authorized"])
            self.assertTrue(all(value is False for value in report["privacy"].values()))
            self.assertTrue(all(value is False for value in report["mutation"].values()))
            self.assertFalse(report["corpus"]["symlink_traversal"])
            self.assertFalse(report["corpus"]["secret_runtime_backup_roots_traversed"])
            self.assertEqual(report["corpus"]["registry_source_whitelist_count"], 1)

            rendered = str(report)
            self.assertNotIn("sensor.unreferenced", rendered)
            self.assertNotIn("sensor.config_ref", rendered)
            self.assertNotIn("private-platform", rendered)
            self.assertNotIn("private-0", rendered)
            self.assertNotIn(str(config_root), rendered)

    def test_no_unreferenced_candidate_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(Path(tmp))
            self.write_registry(
                config_root,
                [
                    "sensor.dash_0",
                    "sensor.config_ref",
                    "sensor.auto_ref",
                    "sensor.multi",
                    "sensor.package_ref",
                    "sensor.template_ref",
                    "sensor.blueprint_ref",
                ],
            )

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], NO_CANDIDATES_DECISION)
            self.assertEqual(report["reasons"], [])
            self.assertEqual(report["coverage"]["unreferenced_in_reviewed_corpus_count"], 0)
            self.assertFalse(report["claims"]["unused_claimed"])

    def test_registry_format_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(Path(tmp))
            (config_root / ".storage" / "core.entity_registry").write_text(
                json.dumps({"data": {"entities": {}}}),
                encoding="utf-8",
            )

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["ENTITY_REGISTRY_FORMAT_DRIFT"])

    def test_recursive_source_symlink_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(Path(tmp))
            link = config_root / "packages" / "linked.yaml"
            try:
                link.symlink_to(config_root / "configuration.yaml")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["RECURSIVE_SOURCE_SYMLINK_PRESENT"])

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
        self.assertIn('"unused_claimed": false', rendered)
        self.assertIn('"registry_modified": false', rendered)
        self.assertIn('"reload_or_restart": false', rendered)


if __name__ == "__main__":
    unittest.main()
