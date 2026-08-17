from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.classify_majas_repeated_structures import (
    BEHAVIORALLY_DISTINCT_PATTERN,
    NO_DEDUP_DECISION,
    PARAMETERIZED_PATTERN,
    build_live_report,
    classify_repeated_structures,
    main,
)
from tools.materialize_majas_dashboard_candidate import (
    dump_yaml,
    load_candidate_tree,
    load_mapping,
)


def card(
    *,
    entity: str,
    name: str,
    shape: str,
    behavior: str | None = None,
    variant: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "custom:neutral-card",
        "entity": entity,
        "name": name,
    }

    if shape == "b":
        payload["icon"] = "mdi:circle"
        payload["tap_action"] = {
            "action": "call-service",
            "service": behavior or "switch.turn_on",
            "target": {"entity_id": entity},
        }
    elif shape == "c":
        payload["secondary"] = "status"
        payload["tap_action"] = {"action": "toggle"}
    elif shape == "unique":
        if variant is None:
            raise ValueError("variant required")
        payload[f"variant_{variant}"] = True

    return payload


def section_payloads() -> list[dict[str, object]]:
    cards = [
        card(entity="sensor.a0", name="A0", shape="a"),
        card(entity="sensor.a1", name="A1", shape="a"),
        card(
            entity="switch.b0",
            name="B0",
            shape="b",
            behavior="switch.turn_on",
        ),
        card(
            entity="switch.b1",
            name="B1",
            shape="b",
            behavior="switch.turn_on",
        ),
        card(
            entity="switch.b2",
            name="B2",
            shape="b",
            behavior="switch.turn_off",
        ),
        card(entity="sensor.c0", name="C0", shape="c"),
        card(entity="sensor.c1", name="C1", shape="c"),
        card(entity="sensor.u0", name="U0", shape="unique", variant=0),
        card(entity="sensor.u1", name="U1", shape="unique", variant=1),
        card(entity="sensor.u2", name="U2", shape="unique", variant=2),
        card(entity="sensor.u3", name="U3", shape="unique", variant=3),
    ]

    return [
        {"type": "grid", "cards": cards[:1]},
        {"type": "grid", "cards": cards[1:6]},
        {"type": "grid", "cards": cards[6:]},
    ]


class MajasRepeatedStructureClassificationTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, Path]:
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
            section_payloads(),
            strict=True,
        ):
            (active_root / "sections" / "view_00" / filename).write_text(
                dump_yaml(payload),
                encoding="utf-8",
            )

        return config_root, active_root

    def test_classification_is_private_safe_and_never_claims_refactor(self):
        with tempfile.TemporaryDirectory() as tmp:
            _config_root, active_root = self.build_fixture(Path(tmp))
            payload = load_candidate_tree(active_root)

            report = classify_repeated_structures(payload)

            self.assertEqual(report["decision"], NO_DEDUP_DECISION)
            self.assertEqual(
                report["reasons"],
                ["NO_SEMANTICS_PRESERVING_REUSE_MECHANISM_PROVEN"],
            )
            summary = report["summary"]
            self.assertEqual(summary["repeated_group_count"], 3)
            self.assertEqual(summary["repeated_member_count"], 7)
            self.assertEqual(summary["parameterized_group_count"], 2)
            self.assertEqual(summary["behaviorally_distinct_group_count"], 1)
            self.assertEqual(summary["bounded_refactor_candidate_count"], 0)
            self.assertFalse(summary["semantics_preserving_reuse_mechanism_proven"])

            groups = report["groups"]
            self.assertEqual(len(groups), 3)
            self.assertEqual(groups[0]["classification"], PARAMETERIZED_PATTERN)
            self.assertFalse(groups[0]["behavioral_surface_present"])
            self.assertTrue(groups[0]["behavioral_surface_identical"])
            self.assertTrue(groups[0]["entity_like_reference_sets_differ"])

            self.assertEqual(groups[1]["classification"], BEHAVIORALLY_DISTINCT_PATTERN)
            self.assertTrue(groups[1]["behavioral_surface_present"])
            self.assertTrue(groups[1]["behavioral_surface_differs"])
            self.assertFalse(groups[1]["differences_confined_to_non_behavioral_scalars"])

            self.assertEqual(groups[2]["classification"], PARAMETERIZED_PATTERN)
            self.assertTrue(groups[2]["behavioral_surface_present"])
            self.assertTrue(groups[2]["behavioral_surface_identical"])

            self.assertTrue(all(not group["exact_duplicate"] for group in groups))
            self.assertTrue(
                all(not group["bounded_refactor_candidate"] for group in groups)
            )
            self.assertTrue(
                all(
                    not group["semantics_preserving_reuse_mechanism_proven"]
                    for group in groups
                )
            )

            rendered = str(report)
            self.assertNotIn("sensor.a0", rendered)
            self.assertNotIn("switch.turn_on", rendered)
            self.assertNotIn("custom:neutral-card", rendered)
            self.assertNotIn("A0", rendered)

    def test_live_report_keeps_phase4a_and_tree_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(Path(tmp))

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], NO_DEDUP_DECISION)
            self.assertTrue(report["home_assistant"]["version_match"])
            self.assertTrue(report["binding"]["resolved"])
            self.assertTrue(report["active_tree"]["exact"])
            self.assertEqual(report["active_tree"]["regular_files"], 5)
            self.assertEqual(report["active_tree"]["directories"], 3)
            self.assertEqual(report["active_tree"]["symlinks"], 0)
            self.assertEqual(report["active_tree"]["unexpected_entries"], 0)

            dashboard = report["dashboard"]
            self.assertEqual(dashboard["guard"]["top_level_card_count"], 11)
            self.assertEqual(dashboard["guard"]["grouping_wrapper_count"], 0)
            self.assertEqual(
                dashboard["phase4a_candidate_summary"],
                {
                    "exact_duplicate_group_count": 0,
                    "repeated_group_count": 3,
                    "repeated_member_count": 7,
                },
            )
            self.assertTrue(all(value is False for value in report["privacy"].values()))
            self.assertTrue(all(value is False for value in report["mutation"].values()))

            rendered = str(report)
            self.assertNotIn("sensor.a0", rendered)
            self.assertNotIn("switch.turn_on", rendered)
            self.assertNotIn(str(config_root), rendered)

    def test_repeated_group_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, active_root = self.build_fixture(Path(tmp))
            target = active_root / "sections" / "view_00" / "20_section.yaml"
            payload = load_mapping(target)
            payload["cards"][0]["shape_breaker"] = True
            target.write_text(dump_yaml(payload), encoding="utf-8")

            report = build_live_report(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["REPEATED_GROUP_COUNT_DRIFT"])

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

    def test_cli_without_classification_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        rendered = stdout.getvalue()
        self.assertIn('"CLASSIFICATION_GATE_REQUIRED"', rendered)
        self.assertIn('"dashboard_modified": false', rendered)
        self.assertIn('"card_or_helper_removed": false', rendered)
        self.assertIn('"reload_or_restart": false', rendered)


if __name__ == "__main__":
    unittest.main()
