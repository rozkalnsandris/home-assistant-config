from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.materialize_majas_dashboard_candidate import dump_yaml
from tools.stratify_unreferenced_core_helpers import (
    NO_CANDIDATES_DECISION,
    READY_DECISION,
    build_live_report,
    main,
)


class CoreHelperProvenanceStratificationTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path]:
        config_root = root / "config"
        active_root = config_root / "majas_modular"
        core_root = root / "core_components"

        (active_root / "views").mkdir(parents=True)
        (active_root / "sections" / "view_00").mkdir(parents=True)
        (config_root / ".storage").mkdir(parents=True)
        core_root.mkdir()

        (config_root / "configuration.yaml").write_text(
            """lovelace:
  mode: yaml
  dashboards:
    majas-yaml:
      mode: yaml
      title: Mājas YAML
      show_in_sidebar: true
      filename: majas_modular/dashboard.yaml
example_reference: sensor.referenced
""",
            encoding="utf-8",
        )
        (config_root / "automations.yaml").write_text("[]\n", encoding="utf-8")
        (config_root / "scripts.yaml").write_text("{}\n", encoding="utf-8")
        (config_root / "scenes.yaml").write_text("[]\n", encoding="utf-8")

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
                "entity": f"sensor.dashboard_{index}",
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

        registry = {
            "version": 1,
            "minor_version": 1,
            "key": "core.entity_registry",
            "data": {
                "entities": [
                    {
                        "entity_id": "sensor.referenced",
                        "platform": "hub_platform",
                        "unique_id": "private-0",
                    },
                    {
                        "entity_id": "sensor.helper_unused",
                        "platform": "helper_platform",
                        "unique_id": "private-1",
                    },
                    {
                        "entity_id": "sensor.hub_unused",
                        "platform": "hub_platform",
                        "unique_id": "private-2",
                    },
                    {
                        "entity_id": "sensor.unknown_unused",
                        "platform": "missing_platform",
                        "unique_id": "private-3",
                    },
                ]
            },
        }
        (config_root / ".storage" / "core.entity_registry").write_text(
            json.dumps(registry),
            encoding="utf-8",
        )

        self.write_manifest(core_root, "helper_platform", "helper")
        self.write_manifest(core_root, "hub_platform", "hub")
        return config_root, core_root

    @staticmethod
    def write_manifest(core_root: Path, domain: str, integration_type: str | None) -> None:
        component = core_root / domain
        component.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "domain": domain,
            "name": "Private test integration",
        }
        if integration_type is not None:
            payload["integration_type"] = integration_type
        (component / "manifest.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def live_report(self, config_root: Path, core_root: Path):
        return build_live_report(
            config_root=config_root,
            dashboard_title="Mājas YAML",
            expected_version="2026.8.2",
            running_version="2026.8.2",
            core_components_root=core_root,
            expected_registry_candidate_count=4,
            expected_referenced_count=1,
            expected_unreferenced_count=3,
            expected_source_file_count=None,
            expected_source_total_bytes=None,
        )

    def test_stratifies_unreferenced_candidates_by_core_manifest_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root = self.build_fixture(Path(tmp))
            report = self.live_report(config_root, core_root)

            self.assertEqual(report["decision"], READY_DECISION)
            self.assertEqual(
                report["reasons"],
                ["CORE_HELPER_UNREFERENCED_CANDIDATES_PRESENT"],
            )
            self.assertEqual(
                report["provenance"]["core_helper_unreferenced_candidate_count"],
                1,
            )
            self.assertEqual(
                report["provenance"]["core_non_helper_unreferenced_candidate_count"],
                1,
            )
            self.assertEqual(
                report["provenance"]["non_core_or_unresolved_unreferenced_candidate_count"],
                1,
            )
            self.assertEqual(report["provenance"]["private_unique_platform_count"], 3)
            self.assertEqual(report["provenance"]["core_manifest_read_count"], 2)
            self.assertFalse(report["manifest_guard"]["hardcoded_helper_allowlist_used"])
            self.assertFalse(report["manifest_guard"]["custom_components_traversed"])
            self.assertTrue(report["manifest_guard"]["manifest_domain_match_required"])
            self.assertTrue(all(value is False for value in report["claims"].values()))
            self.assertTrue(all(value is False for value in report["privacy"].values()))
            self.assertTrue(all(value is False for value in report["mutation"].values()))

            rendered = str(report)
            self.assertNotIn("sensor.helper_unused", rendered)
            self.assertNotIn("helper_platform", rendered)
            self.assertNotIn("hub_platform", rendered)
            self.assertNotIn("missing_platform", rendered)
            self.assertNotIn(str(config_root), rendered)
            self.assertNotIn(str(core_root), rendered)

    def test_no_helper_candidate_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root = self.build_fixture(Path(tmp))
            self.write_manifest(core_root, "helper_platform", "service")

            report = self.live_report(config_root, core_root)

            self.assertEqual(report["decision"], NO_CANDIDATES_DECISION)
            self.assertEqual(report["reasons"], [])
            self.assertEqual(
                report["provenance"]["core_helper_unreferenced_candidate_count"],
                0,
            )
            self.assertEqual(
                report["provenance"]["core_non_helper_unreferenced_candidate_count"],
                2,
            )
            self.assertEqual(
                report["provenance"]["non_core_or_unresolved_unreferenced_candidate_count"],
                1,
            )

    def test_missing_integration_type_is_unresolved_not_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root = self.build_fixture(Path(tmp))
            self.write_manifest(core_root, "helper_platform", None)

            report = self.live_report(config_root, core_root)

            self.assertEqual(report["decision"], NO_CANDIDATES_DECISION)
            self.assertEqual(
                report["provenance"]["core_helper_unreferenced_candidate_count"],
                0,
            )
            self.assertEqual(
                report["provenance"]["non_core_or_unresolved_unreferenced_candidate_count"],
                2,
            )

    def test_manifest_domain_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root = self.build_fixture(Path(tmp))
            manifest = core_root / "helper_platform" / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "domain": "different_private_domain",
                        "integration_type": "helper",
                    }
                ),
                encoding="utf-8",
            )

            report = self.live_report(config_root, core_root)

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["CORE_MANIFEST_DOMAIN_MISMATCH"])
            self.assertTrue(all(value is False for value in report["claims"].values()))

    def test_phase4c_count_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root = self.build_fixture(Path(tmp))

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
                core_components_root=core_root,
                expected_registry_candidate_count=5,
                expected_referenced_count=1,
                expected_unreferenced_count=3,
                expected_source_file_count=None,
                expected_source_total_bytes=None,
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["PHASE4C_REGISTRY_COUNT_DRIFT"])

    def test_version_mismatch_blocks_before_private_resolution(self):
        report = build_live_report(
            config_root=Path("/definitely/private/config"),
            dashboard_title="private",
            expected_version="2026.8.2",
            running_version="2026.8.1",
            core_components_root=Path("/definitely/private/core"),
        )

        self.assertEqual(report["decision"], "BLOCKED")
        self.assertEqual(report["reasons"], ["HOME_ASSISTANT_VERSION_MISMATCH"])

    def test_cli_without_stratify_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        rendered = stdout.getvalue()
        self.assertIn('"STRATIFY_GATE_REQUIRED"', rendered)
        self.assertIn('"helper_removal_candidate_claimed": false', rendered)
        self.assertIn('"registry_modified": false', rendered)
        self.assertIn('"reload_or_restart": false', rendered)


if __name__ == "__main__":
    unittest.main()
