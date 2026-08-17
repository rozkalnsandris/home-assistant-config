from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from tools.materialize_majas_dashboard_candidate import DashboardLoader, dump_yaml
from tools.prepare_majas_sections_flattening_apply import (
    READY_DECISION,
    build_live_apply_preflight,
    main,
    prepare_live_apply,
)


def custom_card(index: int) -> dict[str, object]:
    return {
        "type": "custom:neutral-card",
        "entity": f"sensor.example_{index}",
    }


def section_payloads(*, wrapper_title=None, extra_wrapper_key=None):
    wrapper: dict[str, object] = {
        "type": "grid",
        "columns": 2,
        "square": False,
        "cards": [
            custom_card(8),
            custom_card(9),
            custom_card(10),
            custom_card(11),
        ],
    }
    if wrapper_title is not None:
        wrapper["title"] = wrapper_title
    if extra_wrapper_key is not None:
        wrapper[extra_wrapper_key] = "unexpected"

    return [
        {"type": "grid", "cards": [custom_card(0)]},
        {
            "type": "grid",
            "cards": [
                custom_card(1),
                custom_card(2),
                custom_card(3),
                custom_card(4),
                custom_card(5),
            ],
        },
        {
            "type": "grid",
            "cards": [
                custom_card(6),
                wrapper,
            ],
        },
    ]


def snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class SectionsFlatteningApplyPrepareTests(unittest.TestCase):
    def build_fixture(
        self,
        root: Path,
        *,
        wrapper_title=None,
        extra_wrapper_key=None,
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

        sections = section_payloads(
            wrapper_title=wrapper_title,
            extra_wrapper_key=extra_wrapper_key,
        )
        for filename, payload in zip(
            ("00_section.yaml", "10_section.yaml", "20_section.yaml"),
            sections,
            strict=True,
        ):
            (active_root / "sections" / "view_00" / filename).write_text(
                dump_yaml(payload),
                encoding="utf-8",
            )

        return config_root, active_root

    def test_ready_preflight_builds_private_byte_plan_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, active_root = self.build_fixture(Path(tmp))
            before = snapshot_tree(config_root)

            byte_plan, report = prepare_live_apply(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertIsNotNone(byte_plan)
            assert byte_plan is not None
            self.assertEqual(report["decision"], READY_DECISION)
            self.assertEqual(snapshot_tree(config_root), before)
            self.assertEqual(report["structure"]["before"]["card_count"], 12)
            self.assertEqual(report["structure"]["proposed"]["card_count"], 11)
            self.assertEqual(
                report["structure"]["top_level_card_count_proposed"],
                11,
            )
            self.assertEqual(
                report["structure"]["grouping_wrapper_count_proposed"],
                0,
            )
            self.assertTrue(report["byte_plan"]["active_tree_exact"])
            self.assertEqual(report["byte_plan"]["active_regular_files"], 5)
            self.assertEqual(report["byte_plan"]["active_directories"], 3)
            self.assertTrue(report["byte_plan"]["target_source_file_unique"])
            self.assertTrue(report["byte_plan"]["target_byte_span_unique"])
            self.assertTrue(
                report["byte_plan"]["outside_target_span_bytes_preserved"]
            )
            self.assertTrue(
                report["byte_plan"]["byte_exact_rollback_input_available"]
            )
            self.assertFalse(report["authorization"]["production_write_authorized"])
            self.assertTrue(
                report["authorization"]["explicit_owner_authorization_required"]
            )
            self.assertFalse(report["binding"]["binding_change_planned"])
            self.assertFalse(report["plan"]["grid_options_change_planned"])
            self.assertTrue(report["plan"]["grid_options_count_preserved"])
            self.assertTrue(all(value is False for value in report["mutation"].values()))

            original = byte_plan.original_target_bytes
            proposed = byte_plan.proposed_target_bytes
            self.assertNotEqual(original, proposed)
            self.assertEqual(
                byte_plan.target_section_path,
                active_root / "sections" / "view_00" / "20_section.yaml",
            )
            self.assertEqual(
                byte_plan.target_section_path.read_bytes(),
                original,
            )
            proposed_section = yaml.load(
                proposed.decode("utf-8"),
                Loader=DashboardLoader,
            )
            self.assertEqual(len(proposed_section["cards"]), 5)
            self.assertEqual(
                proposed_section["cards"][1:],
                section_payloads()[2]["cards"][1]["cards"],
            )

            rendered = str(report)
            self.assertNotIn("sensor.example_", rendered)
            self.assertNotIn("custom:neutral-card", rendered)
            self.assertNotIn(str(active_root), rendered)

    def test_title_bearing_wrapper_requires_private_review_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(
                Path(tmp),
                wrapper_title="private heading",
            )
            before = snapshot_tree(config_root)

            report = build_live_apply_preflight(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "NEEDS_PRIVATE_REVIEW")
            self.assertEqual(report["reasons"], ["GRID_WRAPPER_TITLE_PRESENT"])
            self.assertEqual(snapshot_tree(config_root), before)
            self.assertNotIn("private heading", str(report))

    def test_unsupported_wrapper_key_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, _active_root = self.build_fixture(
                Path(tmp),
                extra_wrapper_key="layout_extension",
            )

            report = build_live_apply_preflight(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(
                report["reasons"],
                ["GRID_WRAPPER_KEYS_UNSUPPORTED"],
            )

    def test_active_tree_drift_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_root, active_root = self.build_fixture(Path(tmp))
            (active_root / "unexpected.yaml").write_text(
                "value: true\n",
                encoding="utf-8",
            )

            report = build_live_apply_preflight(
                config_root=config_root,
                dashboard_title="Mājas YAML",
                expected_version="2026.8.2",
                running_version="2026.8.2",
            )

            self.assertEqual(report["decision"], "BLOCKED")
            self.assertEqual(report["reasons"], ["ACTIVE_TREE_MISMATCH"])

    def test_version_mismatch_blocks_before_private_resolution(self):
        report = build_live_apply_preflight(
            config_root=Path("/definitely/not/a/private/config"),
            dashboard_title="private",
            expected_version="2026.8.2",
            running_version="2026.8.1",
        )

        self.assertEqual(report["decision"], "BLOCKED")
        self.assertEqual(
            report["reasons"],
            ["HOME_ASSISTANT_VERSION_MISMATCH"],
        )

    def test_cli_without_prepare_gate_is_inert(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--stdout"])

        self.assertEqual(rc, 1)
        rendered = stdout.getvalue()
        self.assertIn('"PREPARE_GATE_REQUIRED"', rendered)
        self.assertIn('"dashboard_modified": false', rendered)
        self.assertIn('"reload_or_restart": false', rendered)


if __name__ == "__main__":
    unittest.main()
