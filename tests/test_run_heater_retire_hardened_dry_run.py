import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.run_heater_retire_hardened_dry_run import (
    BLOCKED,
    EQUALS_CANDIDATE,
    EQUALS_ORIGINAL,
    READY,
    WORKER_READY,
    blocked_report,
    copy_private_config_tree,
    main,
    scheduler_storage_empty,
    validate_reconciliation_report,
    worker_dry_run,
)


class HardenedRetireDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config_root = self.root / "config"
        self.staged_root = self.root / "staged"
        self.workspace = self.root / "workspace"

        (self.config_root / "packages").mkdir(parents=True)
        (self.config_root / ".storage").mkdir()
        (self.staged_root / "packages").mkdir(parents=True)
        self.workspace.mkdir()

        self.live_one = self.config_root / "packages" / "silditajs.yaml"
        self.live_two = self.config_root / "packages" / "heater_scheduler.yaml"
        self.live_one.write_text("live-one\n", encoding="utf-8")
        self.live_two.write_text("live-two\n", encoding="utf-8")

        self.scheduler = self.config_root / ".storage" / "scheduler.storage"
        self.scheduler.write_text(
            json.dumps({"data": {"schedules": []}}), encoding="utf-8"
        )
        (self.config_root / "configuration.yaml").write_text(
            "homeassistant:\n  packages: !include_dir_named packages\n",
            encoding="utf-8",
        )

        (self.staged_root / "packages" / "silditajs.yaml").write_text(
            self.first_candidate(), encoding="utf-8"
        )
        (self.staged_root / "packages" / "heater_scheduler.yaml").write_text(
            self.second_candidate(), encoding="utf-8"
        )

        self.live_config = {
            "automation": [
                {
                    "id": "silditajs_grafiks_on",
                    "action": [
                        {
                            "service": "switch.turn_on",
                            "target": {
                                "entity_id": "switch.fixture_private_target"
                            },
                        }
                    ],
                },
                {
                    "id": "silditajs_grafiks_off",
                    "action": [
                        {
                            "service": "switch.turn_off",
                            "target": {
                                "entity_id": "switch.fixture_private_target"
                            },
                        }
                    ],
                },
                {
                    "id": "silditajs_auto_off",
                    "action": [
                        {
                            "service": "switch.turn_off",
                            "target": {
                                "entity_id": "switch.fixture_private_target"
                            },
                        }
                    ],
                },
            ]
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def first_candidate(self) -> str:
        return """\
automation:
  - trigger:
      - entity_id: !secret fixture_target_a
    action:
      - condition: state
        entity_id: !secret fixture_target_a
      - service: switch.turn_off
        target:
          entity_id: !secret fixture_target_a
"""

    def second_candidate(self) -> str:
        return """\
input_datetime:
  heater_sched_on_time:
    initial: !secret fixture_initial
script:
  heater_sched_save:
    sequence:
      - variables:
          heater_entity: !secret fixture_target_b
      - service: scheduler.add
        data:
          timeslots: "{{ fixture }}"
"""

    def ready_reconciliation(self) -> dict:
        return {
            "decision": (
                "READY_FOR_OWNER_CHOICE_"
                "PRESERVE_OR_RETIRE_LATENT_LEGACY_SCHEDULE"
            ),
            "reasons": [],
            "home_assistant": {"version_match": True},
            "current_behavior": {
                "legacy_schedule_helper_off": True,
                "scheduler_storage_empty": True,
                "latent_legacy_on_time_valid": True,
                "latent_legacy_off_time_valid": True,
                "recurring_schedule_active": False,
                "legacy_automation_count": 2,
                "legacy_automation_enabled_count": 2,
            },
            "binding_reconciliation": {
                "resolved_value_equality_proof_succeeded": True,
                "all_four_targets_equal": True,
            },
            "source_reconciliation": {
                "legacy_direct_automations_removed": True,
                "legacy_schedule_helper_removed": True,
                "legacy_time_helpers_removed": True,
                "timer_preserved": True,
                "scheduler_save_script_present": True,
                "shared_binding_value_proven_privately": True,
            },
            "transition": {
                "no_bootstrap_preserves_current_active_off_behavior": True,
                "no_bootstrap_retires_latent_legacy_time_values": True,
            },
            "private_resolution": {
                "installed_home_assistant_yaml_loader_used": True,
                "home_assistant_secret_resolution_used": True,
                "private_binding_values_read_for_equality_only": True,
            },
            "privacy": {"fixture": False},
            "mutation": {"fixture": False},
            "claims": {"fixture": False},
        }

    def test_validate_reconciliation_requires_exact_ready_shape(self) -> None:
        report = self.ready_reconciliation()
        self.assertTrue(validate_reconciliation_report(report))
        report["current_behavior"]["recurring_schedule_active"] = True
        self.assertFalse(validate_reconciliation_report(report))

    def test_scheduler_storage_empty(self) -> None:
        self.assertTrue(scheduler_storage_empty(self.scheduler))
        self.scheduler.write_text(
            json.dumps({"data": {"schedules": [{"fixture": True}]}}),
            encoding="utf-8",
        )
        self.assertFalse(scheduler_storage_empty(self.scheduler))

    def test_copy_private_config_tree_keeps_storage_and_excludes_bulk(self) -> None:
        (self.config_root / "home-assistant_v2.db").write_bytes(b"db")
        (self.config_root / "home-assistant.log").write_bytes(b"log")
        (self.config_root / "backups").mkdir()
        (self.config_root / "backups" / "fixture.tar").write_bytes(b"x")

        destination = self.root / "copy"
        copy_private_config_tree(self.config_root, destination)

        self.assertTrue((destination / ".storage" / "scheduler.storage").is_file())
        self.assertTrue((destination / "packages" / "silditajs.yaml").is_file())
        self.assertFalse((destination / "home-assistant_v2.db").exists())
        self.assertFalse((destination / "home-assistant.log").exists())
        self.assertFalse((destination / "backups").exists())

    def test_worker_exercises_hardened_apply_check_and_rollback(self) -> None:
        original_one = self.live_one.read_bytes()
        original_two = self.live_two.read_bytes()
        scheduler_before = self.scheduler.read_bytes()

        with (
            patch(
                "tools.run_heater_retire_hardened_dry_run._load_yaml_dict",
                return_value=self.live_config,
            ),
            patch(
                "tools.run_heater_retire_hardened_dry_run._check_config",
                return_value=True,
            ),
        ):
            report = worker_dry_run(
                self.config_root, self.staged_root, self.workspace
            )

        self.assertEqual(report["decision"], WORKER_READY)
        self.assertTrue(report["full_temp_check_config_passed"])
        self.assertEqual(
            [
                item["classification"]
                for item in report["hardened_apply"]["results"]
            ],
            [EQUALS_CANDIDATE, EQUALS_CANDIDATE],
        )
        self.assertEqual(
            [
                item["classification"]
                for item in report["hardened_rollback"]["results"]
            ],
            [EQUALS_ORIGINAL, EQUALS_ORIGINAL],
        )
        self.assertTrue(report["hardened_rollback"]["temp_originals_restored"])
        self.assertEqual(self.live_one.read_bytes(), original_one)
        self.assertEqual(self.live_two.read_bytes(), original_two)
        self.assertEqual(self.scheduler.read_bytes(), scheduler_before)

        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("switch.fixture_private_target", encoded)
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(
            all(value is False for value in report["production_mutation"].values())
        )

    def test_worker_check_config_failure_still_restores_temp_targets(self) -> None:
        original_one = self.live_one.read_bytes()
        original_two = self.live_two.read_bytes()

        with (
            patch(
                "tools.run_heater_retire_hardened_dry_run._load_yaml_dict",
                return_value=self.live_config,
            ),
            patch(
                "tools.run_heater_retire_hardened_dry_run._check_config",
                return_value=False,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "FULL_TEMP_CHECK_CONFIG_FAILED"
            ):
                worker_dry_run(
                    self.config_root, self.staged_root, self.workspace
                )

        temp_config = self.workspace / "config-copy"
        self.assertEqual(
            (temp_config / "packages" / "silditajs.yaml").read_bytes(),
            original_one,
        )
        self.assertEqual(
            (temp_config / "packages" / "heater_scheduler.yaml").read_bytes(),
            original_two,
        )

    def test_worker_blocks_nonempty_scheduler_before_materialization(self) -> None:
        self.scheduler.write_text(
            json.dumps({"data": {"schedules": [{"fixture": True}]}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "SCHEDULER_STORAGE_NOT_EMPTY"):
            worker_dry_run(self.config_root, self.staged_root, self.workspace)

    def test_blocked_report_is_privacy_safe(self) -> None:
        report = blocked_report("FIXTURE_BLOCK")
        encoded = json.dumps(report, sort_keys=True)
        self.assertEqual(report["decision"], BLOCKED)
        self.assertNotIn("switch.", encoded)
        self.assertNotIn("/config", encoded)
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(
            all(value is False for value in report["production_mutation"].values())
        )

    def test_cli_without_exact_gate_is_fail_closed(self) -> None:
        with patch("sys.stdout") as stdout:
            rc = main([])
        self.assertEqual(rc, 20)
        self.assertTrue(stdout.write.called)

    def test_ready_constant_is_new_production_gate_decision(self) -> None:
        self.assertEqual(
            READY,
            "READY_FOR_NEW_EXPLICIT_RETIRE_PATH_PRODUCTION_APPLY_GATE",
        )


if __name__ == "__main__":
    unittest.main()
