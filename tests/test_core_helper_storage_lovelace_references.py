from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.audit_core_helper_storage_lovelace_references import (
    LovelaceStorageSpec,
    NO_REFERENCES_DECISION,
    REFERENCES_PRESENT_DECISION,
    build_live_report,
    main,
)
from tools.materialize_majas_dashboard_candidate import dump_yaml


class CoreHelperStorageLovelaceReferenceTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path, LovelaceStorageSpec]:
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

        index = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace_dashboards",
            "data": {
                "items": [
                    {
                        "id": "dash1",
                        "url_path": "private-dashboard",
                        "title": "Private dashboard",
                    }
                ]
            },
        }
        self.write_store(config_root, "lovelace_dashboards", index)
        self.write_lovelace_store(
            config_root,
            "lovelace",
            {
                "views": [
                    {
                        "cards": [
                            {
                                "type": "entities",
                                "entities": ["sensor.helper_unused"],
                            }
                        ]
                    }
                ]
            },
        )
        self.write_lovelace_store(
            config_root,
            "lovelace.dash1",
            {
                "views": [
                    {
                        "cards": [
                            {
                                "type": "markdown",
                                "content": "{{ states('sensor.helper_unused') }}",
                            }
                        ]
                    }
                ]
            },
        )

        spec = LovelaceStorageSpec(
            default_key="lovelace",
            named_key_template="lovelace.{}",
            dashboards_key="lovelace_dashboards",
        )
        return config_root, core_root, spec

    @staticmethod
    def write_manifest(core_root: Path, domain: str, integration_type: str) -> None:
        component = core_root / domain
        component.mkdir(parents=True, exist_ok=True)
        (component / "manifest.json").write_text(
            json.dumps(
                {
                    "domain": domain,
                    "name": "Private fixture integration",
                    "integration_type": integration_type,
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def write_store(config_root: Path, key: str, payload: dict) -> None:
        (config_root / ".storage" / key).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    @classmethod
    def write_lovelace_store(cls, config_root: Path, key: str, config: object) -> None:
        cls.write_store(
            config_root,
            key,
            {
                "version": 1,
                "minor_version": 1,
                "key": key,
                "data": {"config": config},
            },
        )

    def live_report(self, config_root: Path, core_root: Path, spec: LovelaceStorageSpec):
        return build_live_report(
            config_root=config_root,
            dashboard_title="Mājas YAML",
            expected_version="2026.8.2",
            running_version="2026.8.2",
            core_components_root=core_root,
            storage_spec=spec,
            expected_registry_candidate_count=4,
            expected_referenced_count=1,
            expected_unreferenced_count=3,
            expected_core_helper_count=1,
            expected_core_non_helper_count=1,
            expected_unresolved_count=1,
            expected_source_file_count=None,
            expected_source_total_bytes=None,
        )

    def test_detects_helper_reference_in_default_and_named_storage_dashboards(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, spec = self.build_fixture(Path(tmp))
            report = self.live_report(config_root, core_root, spec)

            self.assertEqual(report["decision"], REFERENCES_PRESENT_DECISION)
            self.assertEqual(
                report["reasons"],
                ["CORE_HELPER_STORAGE_LOVELACE_REFERENCES_PRESENT"],
            )
            storage = report["storage_lovelace"]
            self.assertTrue(storage["dashboard_index_present"])
            self.assertEqual(storage["allowed_dashboard_store_count"], 2)
            self.assertEqual(storage["inspected_dashboard_store_count"], 2)
            self.assertTrue(storage["default_store_present"])
            self.assertEqual(storage["named_store_count"], 1)
            self.assertEqual(storage["core_helper_candidate_count"], 1)
            self.assertEqual(storage["referenced_candidate_count"], 1)
            self.assertEqual(
                storage["unreferenced_after_yaml_and_storage_lovelace_count"],
                0,
            )
            self.assertEqual(
                storage["referenced_by_multiple_storage_dashboards_count"],
                1,
            )
            self.assertTrue(all(value is False for value in report["claims"].values()))
            self.assertTrue(all(value is False for value in report["privacy"].values()))
            self.assertTrue(all(value is False for value in report["mutation"].values()))
            self.assertFalse(report["storage_scope"]["recursive_storage_traversal"])
            self.assertFalse(report["storage_scope"]["unrelated_storage_files_opened"])

            rendered = str(report)
            for private_value in (
                "sensor.helper_unused",
                "helper_platform",
                "dash1",
                "private-dashboard",
                "Private dashboard",
                str(config_root),
                str(core_root),
            ):
                self.assertNotIn(private_value, rendered)

    def test_no_storage_lovelace_reference_is_not_unused_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, spec = self.build_fixture(Path(tmp))
            self.write_lovelace_store(
                config_root,
                "lovelace",
                {"views": [{"cards": [{"entity": "sensor.some_other_entity"}]}]},
            )
            self.write_lovelace_store(
                config_root,
                "lovelace.dash1",
                {"views": [{"cards": [{"entity": "sensor.another_entity"}]}]},
            )

            report = self.live_report(config_root, core_root, spec)

            self.assertEqual(report["decision"], NO_REFERENCES_DECISION)
            self.assertEqual(report["reasons"], [])
            self.assertEqual(report["storage_lovelace"]["referenced_candidate_count"], 0)
            self.assertEqual(
                report["storage_lovelace"][
                    "unreferenced_after_yaml_and_storage_lovelace_count"
                ],
                1,
            )
            self.assertFalse(report["claims"]["unused_claimed"])
            self.assertFalse(report["claims"]["safe_to_remove_claimed"])

    def test_unrelated_storage_files_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, spec = self.build_fixture(Path(tmp))
            self.write_lovelace_store(config_root, "lovelace", {"views": []})
            self.write_lovelace_store(config_root, "lovelace.dash1", {"views": []})
            (config_root / ".storage" / "auth").write_text(
                "not-json sensor.helper_unused private-secret",
                encoding="utf-8",
            )
            (config_root / ".storage" / "core.config_entries").write_text(
                "not-json sensor.helper_unused private-secret",
                encoding="utf-8",
            )

            report = self.live_report(config_root, core_root, spec)

            self.assertEqual(report["decision"], NO_REFERENCES_DECISION)
            self.assertFalse(report["storage_scope"]["auth_stores_opened"])
            self.assertFalse(report["storage_scope"]["core_config_entries_opened"])
            self.assertFalse(report["storage_scope"]["unrelated_storage_files_opened"])

    def test_missing_index_allows_only_default_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, spec = self.build_fixture(Path(tmp))
            (config_root / ".storage" / "lovelace_dashboards").unlink()
            (config_root / ".storage" / "lovelace.dash1").unlink()

            report = self.live_report(config_root, core_root, spec)

            self.assertEqual(report["decision"], REFERENCES_PRESENT_DECISION)
            storage = report["storage_lovelace"]
            self.assertFalse(storage["dashboard_index_present"])
            self.assertEqual(storage["allowed_dashboard_store_count"], 1)
            self.assertEqual(storage["inspected_dashboard_store_count"], 1)
            self.assertEqual(storage["named_store_count"], 0)

    def test_malformed_dashboard_index_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, spec = self.build_fixture(Path(tmp))
            self.write_store(
                config_root,
                "lovelace_dashboards",
                {
                    "version": 1,
                    "key": "lovelace_dashboards",
                    "data": {"items": "not-a-list"},
                },
            )

            report = self.live_report(config_root, core_root, spec)

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["LOVELACE_DASHBOARD_INDEX_DRIFT"])

    def test_duplicate_dashboard_id_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, spec = self.build_fixture(Path(tmp))
            self.write_store(
                config_root,
                "lovelace_dashboards",
                {
                    "version": 1,
                    "key": "lovelace_dashboards",
                    "data": {"items": [{"id": "dup"}, {"id": "dup"}]},
                },
            )

            report = self.live_report(config_root, core_root, spec)

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["LOVELACE_DASHBOARD_DUPLICATE_ID"])

    def test_storage_symlink_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_root, core_root, spec = self.build_fixture(root)
            outside = root / "outside.json"
            outside.write_text(
                json.dumps({"data": {"config": {"views": []}}}),
                encoding="utf-8",
            )
            lovelace = config_root / ".storage" / "lovelace"
            lovelace.unlink()
            lovelace.symlink_to(outside)

            report = self.live_report(config_root, core_root, spec)

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["LOVELACE_STORAGE_FILE_NOT_REGULAR"])

    def test_invalid_installed_storage_template_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, _spec = self.build_fixture(Path(tmp))
            bad_spec = LovelaceStorageSpec(
                default_key="lovelace",
                named_key_template="../private/{}",
                dashboards_key="lovelace_dashboards",
            )

            report = self.live_report(config_root, core_root, bad_spec)

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(
                report["reasons"],
                ["LOVELACE_NAMED_STORAGE_TEMPLATE_DRIFT"],
            )

    def test_phase4d_helper_count_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, core_root, spec = self.build_fixture(Path(tmp))
            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
                core_components_root=core_root,
                storage_spec=spec,
                expected_registry_candidate_count=4,
                expected_referenced_count=1,
                expected_unreferenced_count=3,
                expected_core_helper_count=2,
                expected_core_non_helper_count=1,
                expected_unresolved_count=1,
                expected_source_file_count=None,
                expected_source_total_bytes=None,
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["PHASE4D_CORE_HELPER_COUNT_DRIFT"])

    def test_version_mismatch_blocks_before_private_resolution(self):
        report = build_live_report(
            config_root=Path("/definitely/private/config"),
            dashboard_title="private",
            expected_version="2026.8.2",
            running_version="2026.8.1",
        )

        self.assertEqual(report["decision"], "BLOCKED")
        self.assertEqual(report["reasons"], ["HOME_ASSISTANT_VERSION_MISMATCH"])

    def test_cli_without_audit_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        rendered = stdout.getvalue()
        self.assertIn('"STORAGE_LOVELACE_AUDIT_GATE_REQUIRED"', rendered)
        self.assertIn('"unused_claimed": false', rendered)
        self.assertIn('"storage_write": false', rendered)
        self.assertIn('"reload_or_restart": false', rendered)


if __name__ == "__main__":
    unittest.main()
