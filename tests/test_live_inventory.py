from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.ha_inventory_payload import classify_entry, inventory, parse_lovelace_refs
from tools.inventory_home_assistant import select_container


class ClassificationTests(unittest.TestCase):
    def test_sensitive_runtime_entries_are_excluded(self) -> None:
        self.assertEqual(classify_entry("secrets.yaml", is_dir=False), ("IGNORE", True))
        self.assertEqual(classify_entry(".storage", is_dir=True), ("IGNORE", True))
        self.assertEqual(
            classify_entry("home-assistant_v2.db-wal", is_dir=False),
            ("BACKUP_ONLY", True),
        )

    def test_declarative_candidates_are_conservative(self) -> None:
        self.assertEqual(
            classify_entry("configuration.yaml", is_dir=False),
            ("TRACK_CANDIDATE", False),
        )
        self.assertEqual(
            classify_entry("dashboards", is_dir=True),
            ("TRACK_CANDIDATE", False),
        )
        self.assertEqual(
            classify_entry("custom_components", is_dir=True),
            ("REVIEW", False),
        )


class LovelaceParsingTests(unittest.TestCase):
    def test_extracts_only_lovelace_dashboard_metadata(self) -> None:
        config = """
default_config:

api:
  password: should-never-be-emitted

lovelace:
  dashboards:
    majas-yaml:
      mode: yaml
      filename: dashboards/majas.yaml
      title: Mājas YAML

http:
  trusted_proxies:
    - 10.0.0.1
"""
        result = parse_lovelace_refs(config)
        self.assertTrue(result["configuration_block_present"])
        self.assertTrue(result["yaml_mode_seen"])
        self.assertEqual(result["dashboard_filenames"], ["dashboards/majas.yaml"])
        self.assertNotIn("should-never-be-emitted", json.dumps(result))
        self.assertNotIn("10.0.0.1", json.dumps(result))


class InventoryTests(unittest.TestCase):
    def test_inventory_does_not_traverse_private_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configuration.yaml").write_text(
                "lovelace:\n  dashboards:\n    home:\n      mode: yaml\n      filename: ui-lovelace.yaml\n",
                encoding="utf-8",
            )
            (root / "ui-lovelace.yaml").write_text("title: Home\n", encoding="utf-8")
            (root / "secrets.yaml").write_text(
                "api_token: SUPER_SECRET_VALUE\n", encoding="utf-8"
            )
            storage = root / ".storage"
            storage.mkdir()
            (storage / "auth").write_text("PRIVATE_RUNTIME_TOKEN", encoding="utf-8")

            result = inventory(root)
            serialized = json.dumps(result, ensure_ascii=False)

            self.assertNotIn("SUPER_SECRET_VALUE", serialized)
            self.assertNotIn("PRIVATE_RUNTIME_TOKEN", serialized)
            self.assertIn("ui-lovelace.yaml", result["dashboard_candidates"])
            self.assertFalse(result["safety"]["secret_values_emitted"])
            self.assertFalse(result["safety"]["runtime_directory_children_traversed"])


class DockerSelectionTests(unittest.TestCase):
    def test_selects_single_home_assistant_image(self) -> None:
        rows = [
            ("grafana", "grafana/grafana:latest"),
            ("ha-prod", "ghcr.io/home-assistant/home-assistant:2026.8.2"),
        ]
        self.assertEqual(select_container(rows), "ha-prod")

    def test_requires_override_when_multiple_candidates_exist(self) -> None:
        rows = [
            ("homeassistant", "ghcr.io/home-assistant/home-assistant:stable"),
            ("home-assistant-test", "ghcr.io/home-assistant/home-assistant:dev"),
        ]
        with self.assertRaises(RuntimeError):
            select_container(rows)
        self.assertEqual(select_container(rows, "homeassistant"), "homeassistant")


if __name__ == "__main__":
    unittest.main()
