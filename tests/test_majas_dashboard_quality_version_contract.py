from __future__ import annotations

import unittest
from pathlib import Path

from tools.audit_majas_dashboard_quality import EXPECTED_HA_VERSION


ROOT = Path(__file__).resolve().parents[1]


class DashboardQualityVersionContractTests(unittest.TestCase):
    def test_audit_default_matches_repository_pin(self):
        pinned = (ROOT / "home-assistant-version.txt").read_text(
            encoding="utf-8"
        ).strip()
        self.assertEqual(EXPECTED_HA_VERSION, pinned)

    def test_current_patch_baseline_is_2026_8_3(self):
        self.assertEqual(EXPECTED_HA_VERSION, "2026.8.3")


if __name__ == "__main__":
    unittest.main()
