"""Synthetic regression tests for the observation-only dashboard audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import audit_majas_dashboard_quality as quality
from tools import audit_majas_dashboard_quality_legacy as legacy
from tools import observe_majas_dashboard_quality as observer
from tools.materialize_majas_dashboard_candidate import TaggedValue


def sample():
    return {
        "views": [{
            "type": "sections",
            "sections": [
                {"cards": [{"type": "custom:example", "section_mode": True}]},
                {"cards": [{
                    "type": "grid",
                    "columns": 2,
                    "cards": [
                        {"type": "custom:example", "name": "PRIVATE_LABEL",
                         "entity": "sensor.private_example"},
                        {"type": "tile", "tap_action": {
                            "action": "perform-action",
                            "perform_action": "lock.unlock",
                            "target": {"entity_id": "lock.private_example"},
                        }},
                    ],
                }]},
            ],
        }],
    }


class ObservationTests(unittest.TestCase):
    def test_observes_grid_without_enabling_legacy_migration(self):
        data = sample()
        original = copy.deepcopy(data)
        report = observer.observe_payload(data)
        self.assertEqual(report["decision"], "OBSERVATION_ONLY")
        self.assertFalse(report["migration_ready"])
        self.assertEqual(report["layout"]["grouping_wrapper_count"], 1)
        self.assertEqual(report["structure"]["card_count"], 4)
        self.assertEqual(report["actions"]["unguarded_higher_impact_count"], 1)
        self.assertEqual(report["modernization"]["action_schema"]["perform_action"], 1)
        self.assertEqual(data, original)
        encoded = json.dumps(report)
        for private in ("PRIVATE_LABEL", "sensor.private_example",
                        "lock.private_example", "lock.unlock", "custom:example"):
            self.assertNotIn(private, encoded)
        with self.assertRaises(legacy.DashboardQualityAuditError):
            quality.analyze_dashboard_quality(data)

    def test_rejects_invalid_nested_declarations(self):
        for bad in ({"type": "tile", "grid_options": {"columns": 99}},
                    {"type": "custom:example", "section_mode": "yes"},
                    {"type": "grid", "cards": "not a list"},
                    {"type": "tile", "entity": TaggedValue("!secret", "private")}):
            with self.subTest(bad=bad):
                data = sample()
                data["views"][0]["sections"][1]["cards"][0]["cards"][0] = bad
                with self.assertRaises(observer.ObservationError):
                    observer.observe_payload(data)

    def test_recursive_value_is_rejected(self):
        data = sample()
        data["cycle"] = data
        with self.assertRaises(observer.ObservationError):
            observer.observe_payload(data)

    def test_modular_registry_and_blocked_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def put(name, content):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            put("configuration.yaml", "lovelace:\n  dashboards:\n    demo:\n      title: Example\n      mode: yaml\n      filename: dashboard/dashboard.yaml\n")
            put("dashboard/dashboard.yaml", "views: !include_dir_list views\n")
            put("dashboard/views/00.yaml", "type: sections\nsections: !include_dir_list ../sections/view_00\n")
            put("dashboard/sections/view_00/00.yaml", "cards:\n  - type: tile\n")
            def report(version=quality.EXPECTED_HA_VERSION):
                return observer.build_report(config_root=root, dashboard_title="Example",
                                             running_version=version)
            self.assertEqual(report()["decision"], "OBSERVATION_ONLY")
            self.assertEqual(report("0.0.0")["decision"], "BLOCKED")
            put("secrets.yaml", "cards: []\n")
            put("dashboard/views/00.yaml", "type: sections\nsections: !include_dir_list ../../\n")
            self.assertEqual(report()["decision"], "BLOCKED")
            put("dashboard/views/00.yaml", "type: sections\nsections: !include_dir_list ../sections/view_00\n")
            link = root / "dashboard/sections/view_00/01.yaml"
            link.symlink_to(root / "secrets.yaml")
            result = report()
            self.assertEqual(result["decision"], "BLOCKED")
            self.assertNotIn(str(root), json.dumps(result))
            self.assertNotIn("secrets.yaml", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
