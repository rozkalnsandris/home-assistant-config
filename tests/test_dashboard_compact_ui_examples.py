"""Static regression checks for public-safe compact dashboard examples."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "dashboard-compact-ui-regressions.yaml"
)


def properties(entries: list[dict[str, object]]) -> dict[str, object]:
    """Flatten a button-card list of single-property CSS declarations."""
    return {key: value for entry in entries for key, value in entry.items()}


class CompactUiExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = FIXTURE.read_text(encoding="utf-8")
        cls.templates = yaml.safe_load(cls.raw)["button_card_templates"]

    def test_greeting_owns_full_first_row(self) -> None:
        header = self.templates["compact_header_example"]
        grid = properties(header["styles"]["grid"])
        # Split by individual quoted CSS grid rows, not arbitrary whitespace.
        rows = re.findall(r'"([^"]+)"', grid["grid-template-areas"])
        self.assertEqual(rows, ["g g g", "a b c", "d d d"])
        self.assertEqual(
            grid["grid-template-columns"], "repeat(3, minmax(0, 1fr))"
        )
        self.assertEqual(grid["grid-template-rows"], "auto auto auto")
        self.assertEqual(len(rows), 3)
        for name in ("g", "a", "b", "c", "d"):
            self.assertIn(name, header["styles"]["custom_fields"])
        self.assertEqual(
            properties(header["styles"]["custom_fields"]["g"])["white-space"],
            "normal",
        )
        nested = header["custom_fields"]["g"]["card"]
        self.assertEqual(properties(nested["styles"]["name"])["white-space"], "normal")
        self.assertNotIn("tap_action", nested)

    def test_numeric_label_rejects_bad_values(self) -> None:
        label = self.templates["finite_temperature_label_example"]["label"]
        for expected in (
            "Number.isFinite(value)",
            "Number.isFinite(parsed)",
            "value.trim() === ''",
            "readNumber(primary?.state)",
            "readNumber(fallback?.attributes?.current_temperature)",
            "current !== null ? current : secondary",
            "result === null ? '—'",
        ):
            self.assertIn(expected, label)
        self.assertNotIn("parseFloat(", label)

    def test_public_fixture_has_no_real_bindings_or_mutating_actions(self) -> None:
        self.assertNotRegex(
            self.raw,
            r"\b(?:sensor|binary_sensor|climate|switch|input_select|person)"
            r"\.[a-zA-Z0-9_]+",
        )
        self.assertNotRegex(self.raw, r"https?://|/config/|/home/")
        self.assertNotIn("call-service", self.raw)
        self.assertNotIn("perform-action", self.raw)
        self.assertNotIn("tap_action:", self.raw)


if __name__ == "__main__":
    unittest.main()
