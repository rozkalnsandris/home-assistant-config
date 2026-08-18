import json
import unittest

from tools.diagnose_heater_scheduler_sequence_ordinals import (
    BLOCKED,
    DIAGNOSTIC_COMPLETE,
    build_report,
    classify_sequence_ordinals,
)


class SchedulerSequenceOrdinalDiagnosticTests(unittest.TestCase):
    def base_config(self) -> dict:
        return {
            "script": {
                "heater_sched_save": {
                    "sequence": [
                        {"variables": {"fixture_target": "switch.private_fixture"}},
                        {"service": "persistent_notification.create"},
                        {
                            "service": "scheduler.add",
                            "data": {"timeslots": "{{ fixture }}"},
                        },
                        {"service": "persistent_notification.create"},
                    ]
                }
            }
        }

    def test_unique_variable_scalar_is_localized_by_ordinal(self) -> None:
        shape = classify_sequence_ordinals(self.base_config())
        self.assertEqual(shape["shape_reason"], "UNIQUE_ENTITY_REFERENCE_ORDINAL")
        self.assertEqual(shape["sequence_step_count"], 4)
        self.assertEqual(shape["entity_reference_scalar_total"], 1)
        self.assertEqual(shape["entity_reference_populated_ordinal_count"], 1)
        self.assertEqual(shape["scheduler_add_ordinal_count"], 1)
        self.assertEqual(shape["ordinals"][0]["variables_entity_reference_scalar_count"], 1)
        self.assertEqual(shape["ordinals"][0]["entity_reference_scalar_count"], 1)
        self.assertFalse(shape["ordinals"][0]["scheduler_add_step"])
        self.assertTrue(shape["ordinals"][2]["scheduler_add_step"])
        self.assertEqual(shape["ordinals"][2]["entity_reference_scalar_count"], 0)

    def test_target_mapping_scalar_is_counted_without_value(self) -> None:
        config = self.base_config()
        config["script"]["heater_sched_save"]["sequence"][0] = {
            "service": "switch.turn_off",
            "target": {"entity_id": "switch.private_fixture"},
        }
        shape = classify_sequence_ordinals(config)
        row = shape["ordinals"][0]
        self.assertEqual(row["target_key_count"], 1)
        self.assertEqual(row["target_mapping_count"], 1)
        self.assertEqual(row["target_entity_reference_scalar_count"], 1)
        self.assertEqual(row["entity_id_key_count"], 1)
        self.assertEqual(row["entity_id_scalar_count"], 1)
        self.assertEqual(row["entity_reference_scalar_count"], 1)

    def test_service_and_action_values_are_excluded(self) -> None:
        config = self.base_config()
        config["script"]["heater_sched_save"]["sequence"][0] = {
            "service": "switch.turn_on",
            "action": "switch.turn_off",
        }
        shape = classify_sequence_ordinals(config)
        self.assertEqual(shape["ordinals"][0]["entity_reference_scalar_count"], 0)

    def test_multiple_scalar_ordinals_are_classified(self) -> None:
        config = self.base_config()
        config["script"]["heater_sched_save"]["sequence"][1] = {
            "variables": {"second_fixture": "input_datetime.private_fixture"}
        }
        shape = classify_sequence_ordinals(config)
        self.assertEqual(shape["shape_reason"], "MULTIPLE_ENTITY_REFERENCE_ORDINALS")
        self.assertEqual(shape["entity_reference_scalar_total"], 2)
        self.assertEqual(shape["entity_reference_populated_ordinal_count"], 2)

    def test_scheduler_add_with_no_target_stays_zero(self) -> None:
        shape = classify_sequence_ordinals(self.base_config())
        scheduler_rows = [row for row in shape["ordinals"] if row["scheduler_add_step"]]
        self.assertEqual(len(scheduler_rows), 1)
        self.assertEqual(scheduler_rows[0]["entity_reference_scalar_count"], 0)
        self.assertEqual(scheduler_rows[0]["entity_id_key_count"], 0)
        self.assertEqual(scheduler_rows[0]["target_key_count"], 0)

    def test_report_is_sanitized(self) -> None:
        probe = {
            "runtime_error": False,
            "installed_home_assistant_yaml_loader_used": True,
            "home_assistant_secret_resolution_used": True,
            "shape": classify_sequence_ordinals(self.base_config()),
        }
        report = build_report(probe, expected="2026.8.2", running="2026.8.2")
        encoded = json.dumps(report, sort_keys=True)
        self.assertEqual(report["decision"], DIAGNOSTIC_COMPLETE)
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(all(value is False for value in report["mutation"].values()))
        self.assertNotIn("switch.private_fixture", encoded)
        self.assertNotIn("fixture_target", encoded)
        self.assertNotIn("{{ fixture }}", encoded)

    def test_version_mismatch_blocks(self) -> None:
        probe = {
            "runtime_error": False,
            "installed_home_assistant_yaml_loader_used": True,
            "home_assistant_secret_resolution_used": True,
            "shape": classify_sequence_ordinals(self.base_config()),
        }
        report = build_report(probe, expected="2026.8.2", running="2026.8.3")
        self.assertEqual(report["decision"], BLOCKED)
        self.assertEqual(report["reasons"], ["HOME_ASSISTANT_VERSION_MISMATCH"])


if __name__ == "__main__":
    unittest.main()
