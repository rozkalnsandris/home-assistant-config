"""Privacy and schema checks for the non-production climate popup example."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "bubble-climate-popup-preview.yaml"
)
PLACEHOLDER = "REPLACE_WITH_PRIVATE_CLIMATE_ENTITY"


class BubbleClimatePopupPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = EXAMPLE.read_text(encoding="utf-8")
        cls.payload = yaml.safe_load(cls.source)

    def test_browser_mod_opens_one_adaptive_popup(self) -> None:
        card = self.payload
        self.assertEqual(card["type"], "custom:button-card")
        self.assertEqual(card["tap_action"]["action"], "fire-dom-event")
        browser_mod = card["tap_action"]["browser_mod"]
        self.assertEqual(browser_mod["service"], "browser_mod.popup")
        data = browser_mod["data"]
        self.assertIs(data["adaptive"], True)
        self.assertIs(data["adaptive_force_bottom_sheet"], True)
        self.assertIs(data["adaptive_allow_mode_change"], False)
        self.assertIs(data["dismissable"], True)

    def test_climate_controls_follow_documented_bubble_card_schema(self) -> None:
        climate = self.payload["tap_action"]["browser_mod"]["data"]["content"]
        self.assertEqual(climate["type"], "custom:bubble-card")
        self.assertEqual(climate["card_type"], "climate")
        self.assertEqual(climate["entity"], PLACEHOLDER)
        self.assertEqual(climate["main_buttons_position"], "bottom")
        self.assertIs(climate["main_buttons_full_width"], True)
        self.assertEqual(
            climate["sub_button"],
            [
                {
                    "name": "HVAC modes",
                    "select_attribute": "hvac_modes",
                    "show_arrow": False,
                    "state_background": False,
                }
            ],
        )
        # Do not invent available setpoints or HVAC/preset modes.
        for forbidden in ("step", "min_temp", "max_temp", "hvac_mode", "preset_mode"):
            self.assertNotIn(forbidden, climate)

    def test_no_private_bindings_or_direct_actuation(self) -> None:
        self.assertIn("PUBLIC EXAMPLE ONLY", self.source)
        self.assertIn("separate LIVE authorization", self.source)
        self.assertIn("NOT guaranteed", self.source)
        self.assertNotRegex(
            self.source,
            re.compile(
                r"\b(?:climate|sensor|binary_sensor|switch|person|camera)"
                r"\.[a-z0-9_]+",
                re.IGNORECASE,
            ),
        )
        self.assertNotRegex(self.source, r"/config/|/home/|https?://[^ ]*\?")
        self.assertNotIn("entity_id:", self.source)
        self.assertNotIn("call-service", self.source)
        self.assertNotIn("perform-action", self.source)
        self.assertNotIn("climate.set_temperature", self.source)


if __name__ == "__main__":
    unittest.main()
