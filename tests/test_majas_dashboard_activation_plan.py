import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.plan_majas_dashboard_activation import (
    build_binding_plan,
    main,
    plan_activation,
)


def custom_card(label: str) -> dict:
    return {
        "type": "custom:synthetic-card",
        "label": f"Synthetic {label}",
    }


def fixture() -> dict:
    return {
        "title": "Synthetic dashboard",
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


def validator(
    _dashboard: Path,
    _root: Path,
    version: str,
) -> dict[str, bool]:
    return {
        "version_match": version == "2026.8.2",
        "candidate_parses": True,
    }


def write_candidate(root: Path, payload: dict | None = None) -> Path:
    payload = payload or fixture()
    candidate = root / "candidate"
    if candidate.exists():
        shutil.rmtree(candidate)
    (candidate / "views").mkdir(parents=True)
    section_root = candidate / "sections" / "view_00"
    section_root.mkdir(parents=True)

    view = payload["views"][0]
    sections = view["sections"]

    dashboard_root = {
        key: value
        for key, value in payload.items()
        if key != "views"
    }
    dashboard_text = yaml.safe_dump(
        dashboard_root,
        sort_keys=False,
        allow_unicode=True,
    )
    dashboard_text += "views: !include_dir_list views\n"
    (candidate / "dashboard.yaml").write_text(
        dashboard_text,
        encoding="utf-8",
    )

    view_root = {
        key: value
        for key, value in view.items()
        if key != "sections"
    }
    view_text = yaml.safe_dump(
        view_root,
        sort_keys=False,
        allow_unicode=True,
    )
    view_text += "sections: !include_dir_list ../sections/view_00\n"
    (candidate / "views" / "00_view.yaml").write_text(
        view_text,
        encoding="utf-8",
    )

    for name, section in zip(
        (
            "00_section.yaml",
            "10_section.yaml",
            "20_section.yaml",
        ),
        sections,
        strict=True,
    ):
        (section_root / name).write_text(
            yaml.safe_dump(
                section,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    return candidate


class ActivationFixture:
    def __init__(
        self,
        *,
        include_lovelace: bool = True,
        quote_filename: str | None = None,
    ) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.active = self.root / "active.yaml"
        self.active.write_text(
            yaml.safe_dump(
                fixture(),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        self.candidate = write_candidate(self.root)

        if quote_filename == "single":
            filename = "'active.yaml'"
        elif quote_filename == "double":
            filename = '"active.yaml"'
        else:
            filename = "active.yaml"

        if include_lovelace:
            (self.root / "configuration.yaml").write_text(
                "default_config:\n"
                "lovelace: !include lovelace.yaml\n",
                encoding="utf-8",
            )
            self.owner = self.root / "lovelace.yaml"
            self.owner.write_text(
                "# keep this comment\n"
                "resource_mode: yaml\n"
                "dashboards:\n"
                "  synthetic:\n"
                "    mode: yaml\n"
                "    title: Synthetic Dashboard\n"
                f"    filename: {filename}\n"
                "    show_in_sidebar: true\n",
                encoding="utf-8",
            )
        else:
            self.owner = self.root / "configuration.yaml"
            self.owner.write_text(
                "# root comment\n"
                "default_config:\n"
                "lovelace:\n"
                "  resource_mode: yaml\n"
                "  dashboards:\n"
                "    synthetic:\n"
                "      mode: yaml\n"
                "      title: Synthetic Dashboard\n"
                f"      filename: {filename}\n"
                "      show_in_sidebar: true\n"
                "automation: !include automations.yaml\n",
                encoding="utf-8",
            )
            (self.root / "automations.yaml").write_text(
                "[]\n",
                encoding="utf-8",
            )

    def cleanup(self) -> None:
        self.tmp.cleanup()


class MajasActivationPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ActivationFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def run_success(self) -> dict:
        return plan_activation(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            candidate_root=self.fx.candidate,
            validator=validator,
        )

    def test_included_lovelace_plan_is_ready_and_inert(self) -> None:
        before = self.fx.owner.read_bytes()
        report = self.run_success()
        self.assertEqual(
            report["decision"],
            "READY_FOR_PRIVATE_ACTIVATION_DRY_RUN",
        )
        self.assertEqual(report["owner"]["kind"], "LOVELACE_INCLUDE")
        self.assertEqual(self.fx.owner.read_bytes(), before)
        self.assertFalse(
            report["mutation"]["live_dashboard_binding_changed"]
        )
        self.assertFalse(report["mutation"]["reload_or_restart"])

    def test_direct_configuration_owner_is_supported(self) -> None:
        self.fx.cleanup()
        self.fx = ActivationFixture(include_lovelace=False)
        report = self.run_success()
        self.assertEqual(
            report["decision"],
            "READY_FOR_PRIVATE_ACTIVATION_DRY_RUN",
        )
        self.assertEqual(report["owner"]["kind"], "CONFIGURATION_ROOT")

    def test_patch_changes_only_filename_scalar_bytes(self) -> None:
        original = self.fx.owner.read_bytes()
        plan = build_binding_plan(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            candidate_root=self.fx.candidate,
        )
        proposed = plan.proposed_owner_bytes
        replacement = plan.proposed_filename.encode("utf-8")
        self.assertEqual(
            proposed[: plan.scalar_start],
            original[: plan.scalar_start],
        )
        self.assertEqual(
            proposed[
                plan.scalar_start + len(replacement) :
            ],
            original[plan.scalar_end :],
        )
        self.assertIn(b"# keep this comment", proposed)

    def test_unicode_before_filename_keeps_byte_boundaries(self) -> None:
        original = self.fx.owner.read_text(encoding="utf-8")
        self.fx.owner.write_text(
            original.replace(
                "title: Synthetic Dashboard",
                "title: Mājas YAML",
            ),
            encoding="utf-8",
        )
        before = self.fx.owner.read_bytes()
        plan = build_binding_plan(
            config_root=self.fx.root,
            dashboard_title="Mājas YAML",
            candidate_root=self.fx.candidate,
        )
        replacement = plan.proposed_filename.encode("utf-8")
        self.assertEqual(
            plan.proposed_owner_bytes[: plan.scalar_start],
            before[: plan.scalar_start],
        )
        self.assertEqual(
            plan.proposed_owner_bytes[
                plan.scalar_start + len(replacement) :
            ],
            before[plan.scalar_end :],
        )

    def test_single_quote_style_is_preserved(self) -> None:
        self.fx.cleanup()
        self.fx = ActivationFixture(quote_filename="single")
        plan = build_binding_plan(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            candidate_root=self.fx.candidate,
        )
        line = next(
            item
            for item in plan.proposed_owner_bytes.decode().splitlines()
            if "filename:" in item
        )
        self.assertIn("filename: 'candidate/dashboard.yaml'", line)

    def test_double_quote_style_is_preserved(self) -> None:
        self.fx.cleanup()
        self.fx = ActivationFixture(quote_filename="double")
        plan = build_binding_plan(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            candidate_root=self.fx.candidate,
        )
        line = next(
            item
            for item in plan.proposed_owner_bytes.decode().splitlines()
            if "filename:" in item
        )
        self.assertIn('filename: "candidate/dashboard.yaml"', line)

    def test_candidate_content_drift_is_blocked(self) -> None:
        payload = fixture()
        payload["views"][0]["sections"][0]["cards"][0]["label"] = (
            "Drifted"
        )
        self.fx.candidate = write_candidate(
            self.fx.root,
            payload,
        )
        report = self.run_success()
        self.assertEqual(
            report["reasons"],
            ["CANDIDATE_NOT_EQUIVALENT"],
        )

    def test_active_shape_drift_is_blocked(self) -> None:
        payload = fixture()
        payload["views"][0]["sections"].pop()
        self.fx.active.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
        report = self.run_success()
        self.assertEqual(
            report["reasons"],
            ["ACTIVE_STRUCTURE_MISMATCH"],
        )

    def test_extra_candidate_file_is_blocked(self) -> None:
        (self.fx.candidate / "extra.yaml").write_text(
            "{}\n",
            encoding="utf-8",
        )
        report = self.run_success()
        self.assertEqual(
            report["reasons"],
            ["CANDIDATE_TREE_MISMATCH"],
        )

    def test_candidate_outside_config_is_blocked(self) -> None:
        outside_tmp = tempfile.TemporaryDirectory()
        try:
            outside = write_candidate(Path(outside_tmp.name))
            report = plan_activation(
                config_root=self.fx.root,
                dashboard_title="Synthetic Dashboard",
                candidate_root=outside,
                validator=validator,
            )
            self.assertEqual(
                report["reasons"],
                ["CANDIDATE_OUTSIDE_CONFIG"],
            )
        finally:
            outside_tmp.cleanup()

    def test_validator_failure_is_blocked_without_mutation(self) -> None:
        before = self.fx.owner.read_bytes()

        def failure(
            _dashboard: Path,
            _root: Path,
            _version: str,
        ) -> dict[str, bool]:
            return {
                "version_match": False,
                "candidate_parses": True,
            }

        report = plan_activation(
            config_root=self.fx.root,
            dashboard_title="Synthetic Dashboard",
            candidate_root=self.fx.candidate,
            validator=failure,
        )
        self.assertEqual(
            report["reasons"],
            ["HOME_ASSISTANT_VALIDATION_FAILED"],
        )
        self.assertEqual(self.fx.owner.read_bytes(), before)

    def test_success_report_is_sanitized(self) -> None:
        report = self.run_success()
        rendered = str(report)
        self.assertNotIn("Synthetic Dashboard", rendered)
        self.assertNotIn("active.yaml", rendered)
        self.assertNotIn("candidate/dashboard.yaml", rendered)
        self.assertFalse(report["privacy"]["private_paths_emitted"])
        self.assertFalse(report["privacy"]["binding_values_emitted"])
        self.assertTrue(
            report["rollback"][
                "exact_original_owner_bytes_captured_in_memory"
            ]
        )

    def test_cli_without_plan_is_inert(self) -> None:
        before = self.fx.owner.read_bytes()
        exit_code = main(
            [
                "--config-root",
                str(self.fx.root),
                "--dashboard-title",
                "Synthetic Dashboard",
                "--candidate-root",
                str(self.fx.candidate),
            ]
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(self.fx.owner.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
