import tempfile
import unittest
from pathlib import Path

from tools.materialize_majas_dashboard_candidate import (
    TaggedValue,
    dump_yaml,
    load_candidate_tree,
    main,
    materialize_candidate,
)


def custom_card(label: str) -> dict:
    return {"type": "custom:synthetic-card", "label": f"Synthetic {label}"}


def fixture() -> dict:
    return {
        "title": "Synthetic dashboard",
        "background": TaggedValue("!synthetic", "opaque"),
        "views": [
            {
                "title": "Synthetic view",
                "path": "synthetic",
                "type": "sections",
                "sections": [
                    {
                        "type": "grid",
                        "cards": [
                            custom_card("a"),
                            custom_card("b"),
                            custom_card("c"),
                            custom_card("d"),
                            custom_card("e"),
                            custom_card("f"),
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            custom_card("g"),
                            custom_card("h"),
                            custom_card("i"),
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            custom_card("j"),
                            custom_card("k"),
                            {"type": "grid", "cards": []},
                        ],
                    },
                ],
            }
        ],
    }


def validator(_dashboard: Path, _root: Path, version: str) -> dict[str, bool]:
    return {
        "version_match": version == "2026.8.2",
        "candidate_parses": True,
    }


class CandidateFixture:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "configuration.yaml").write_text(
            "lovelace: !include lovelace.yaml\n",
            encoding="utf-8",
        )
        (self.root / "lovelace.yaml").write_text(
            "dashboards:\n"
            "  synthetic:\n"
            "    mode: yaml\n"
            "    title: Synthetic Dashboard\n"
            "    filename: active.yaml\n",
            encoding="utf-8",
        )
        (self.root / "active.yaml").write_text(
            dump_yaml(fixture()),
            encoding="utf-8",
        )
        self.source_before = (self.root / "active.yaml").read_bytes()

    def cleanup(self) -> None:
        self.tmp.cleanup()


class MajasCandidateMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = CandidateFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def run_success(self, name: str = "candidate") -> dict:
        return materialize_candidate(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            destination=Path(name),
            validator=validator,
        )

    def test_materializes_exact_ordered_tree_and_round_trip(self) -> None:
        report = self.run_success()
        self.assertEqual(
            report["decision"],
            "READY_FOR_PRIVATE_CANDIDATE_REVIEW",
        )
        candidate = self.fx.root / "candidate"
        self.assertEqual(
            sorted(
                str(path.relative_to(candidate))
                for path in candidate.rglob("*.yaml")
            ),
            [
                "dashboard.yaml",
                "sections/view_00/00_section.yaml",
                "sections/view_00/10_section.yaml",
                "sections/view_00/20_section.yaml",
                "views/00_view.yaml",
            ],
        )
        self.assertEqual(load_candidate_tree(candidate), fixture())

    def test_preserves_counts_and_section_order(self) -> None:
        report = self.run_success()
        self.assertEqual(
            report["structure"]["before"],
            report["structure"]["after"],
        )
        self.assertEqual(report["structure"]["before"]["view_count"], 1)
        self.assertEqual(report["structure"]["before"]["section_count"], 3)
        self.assertEqual(report["structure"]["before"]["card_count"], 12)
        self.assertEqual(
            report["structure"]["before"]["custom_card_count"],
            11,
        )
        self.assertEqual(
            report["structure"]["before"]["distinct_custom_card_type_count"],
            1,
        )
        self.assertEqual(report["structure"]["ordered_section_count"], 3)

    def test_unknown_yaml_tag_survives_materialization(self) -> None:
        self.run_success()
        assembled = load_candidate_tree(self.fx.root / "candidate")
        self.assertEqual(
            assembled["background"],
            TaggedValue("!synthetic", "opaque"),
        )

    def test_destination_outside_config_is_rejected(self) -> None:
        outside = self.fx.root.parent / "outside-candidate"
        report = materialize_candidate(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            destination=outside,
            validator=validator,
        )
        self.assertEqual(report["reasons"], ["DESTINATION_OUTSIDE_CONFIG"])
        self.assertFalse(outside.exists())

    def test_existing_destination_is_rejected_without_touching_it(self) -> None:
        existing = self.fx.root / "candidate"
        existing.mkdir()
        marker = existing / "marker.txt"
        marker.write_text("keep", encoding="utf-8")
        report = self.run_success()
        self.assertEqual(report["reasons"], ["DESTINATION_ALREADY_EXISTS"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_shape_drift_is_rejected_before_write(self) -> None:
        payload = fixture()
        payload["views"][0]["sections"].pop()
        (self.fx.root / "active.yaml").write_text(
            dump_yaml(payload),
            encoding="utf-8",
        )
        report = self.run_success()
        self.assertEqual(report["reasons"], ["BASELINE_STRUCTURE_MISMATCH"])
        self.assertFalse((self.fx.root / "candidate").exists())

    def test_partial_write_failure_cleans_only_candidate(self) -> None:
        unrelated = self.fx.root / "unrelated.txt"
        unrelated.write_text("keep", encoding="utf-8")

        def fail_after_second_write(index: int, _path: Path) -> None:
            if index == 2:
                raise OSError("synthetic failure")

        report = materialize_candidate(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            destination=Path("candidate"),
            validator=validator,
            write_hook=fail_after_second_write,
        )
        self.assertTrue(
            report["mutation"]["candidate_tree_cleaned_after_failure"]
        )
        self.assertFalse((self.fx.root / "candidate").exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_active_dashboard_is_never_modified(self) -> None:
        self.run_success()
        self.assertEqual(
            (self.fx.root / "active.yaml").read_bytes(),
            self.fx.source_before,
        )

    def test_validator_failure_removes_new_candidate(self) -> None:
        def mismatch(
            _dashboard: Path,
            _root: Path,
            _version: str,
        ) -> dict[str, bool]:
            return {"version_match": False, "candidate_parses": True}

        report = materialize_candidate(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            destination=Path("candidate"),
            validator=mismatch,
        )
        self.assertEqual(
            report["reasons"],
            ["HOME_ASSISTANT_VALIDATION_FAILED"],
        )
        self.assertFalse((self.fx.root / "candidate").exists())

    def test_report_is_sanitized_and_does_not_claim_live_mutation(self) -> None:
        report = self.run_success()
        rendered = str(report)
        self.assertNotIn("Synthetic Dashboard", rendered)
        self.assertNotIn("Synthetic view", rendered)
        self.assertNotIn("custom:synthetic-card", rendered)
        self.assertFalse(report["privacy"]["raw_private_values_emitted"])
        self.assertFalse(report["privacy"]["private_paths_emitted"])
        self.assertFalse(report["mutation"]["active_dashboard_modified"])
        self.assertFalse(
            report["mutation"]["live_dashboard_binding_changed"]
        )
        self.assertFalse(report["mutation"]["storage_write"])
        self.assertFalse(report["mutation"]["reload_or_restart"])

    def test_relative_destination_parent_must_already_exist(self) -> None:
        report = materialize_candidate(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            destination=Path("missing-parent/candidate"),
            validator=validator,
        )
        self.assertEqual(
            report["reasons"],
            ["DESTINATION_PARENT_UNAVAILABLE"],
        )

    def test_cli_without_materialize_is_inert(self) -> None:
        exit_code = main(
            [
                "--config-root",
                str(self.fx.root),
                "--dashboard-title",
                "Synthetic Dashboard",
                "--destination",
                "candidate",
            ]
        )
        self.assertEqual(exit_code, 1)
        self.assertFalse((self.fx.root / "candidate").exists())

    def test_success_report_has_exact_five_candidate_files(self) -> None:
        report = self.run_success()
        self.assertEqual(report["structure"]["candidate_file_count"], 5)
        self.assertTrue(
            report["validation"]["candidate_round_trip_equivalent"]
        )
        self.assertTrue(
            report["validation"]["home_assistant_version_match"]
        )
        self.assertTrue(
            report["validation"]["home_assistant_candidate_parses"]
        )


if __name__ == "__main__":
    unittest.main()
