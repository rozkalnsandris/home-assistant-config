import json
import unittest

from tools.diagnose_heater_scheduler_add_subtree import (
    BLOCKED,
    DIAGNOSTIC_COMPLETE,
    build_report,
    classify_scheduler_add_subtree,
)


def direct_target_config() -> dict:
    return {
        "script": {
            "heater_sched_save": {
                "sequence": [
                    {
                        "variables": {
                            "helper_ref": "input_datetime.fixture_time",
                        }
                    },
                    {
                        "service": "scheduler.add",
                        "data": {
                            "timeslots": [
                                {
                                    "start": "00:00:00",
                                    "actions": [
                                        {
                                            "entity_id": "switch.private_fixture",
                                            "service": "switch.turn_on",
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                ]
            }
        }
    }


def variable_target_config() -> dict:
    return {
        "script": {
            "heater_sched_save": {
                "sequence": [
                    {
                        "variables": {
                            "heater_entity": "switch.private_fixture",
                            "helper_ref": "input_datetime.fixture_time",
                        }
                    },
                    {
                        "service": "scheduler.add",
                        "data": {"timeslots": "{{ fixture_template }}"},
                    },
                ]
            }
        }
    }


class SchedulerAddSubtreeDiagnosticTests(unittest.TestCase):
    def test_direct_scheduler_add_target_is_unique(self) -> None:
        shape = classify_scheduler_add_subtree(direct_target_config())

        self.assertEqual(
            shape["shape_reason"], "UNIQUE_ENTITY_ID_SCALAR_UNDER_SCHEDULER_ADD"
        )
        self.assertEqual(shape["scheduler_add_mapping_count"], 1)
        self.assertEqual(shape["scheduler_add_data_mapping_count"], 1)
        self.assertEqual(shape["scheduler_add_timeslots_key_count"], 1)
        self.assertEqual(shape["scheduler_add_timeslots_list_count"], 1)
        self.assertEqual(shape["scheduler_add_actions_key_count"], 1)
        self.assertEqual(shape["scheduler_add_actions_list_count"], 1)
        self.assertEqual(shape["scheduler_add_action_entry_mapping_count"], 1)
        self.assertEqual(shape["scheduler_add_entity_id_key_count"], 1)
        self.assertEqual(shape["scheduler_add_entity_id_scalar_count"], 1)
        self.assertEqual(shape["scheduler_add_entity_scalar_count"], 2)
        self.assertEqual(shape["scheduler_add_timeslots_entity_scalar_count"], 2)
        self.assertEqual(shape["scheduler_add_actions_entity_scalar_count"], 2)

    def test_helper_scalar_outside_scheduler_add_does_not_count(self) -> None:
        config = direct_target_config()
        config["script"]["heater_sched_save"]["sequence"][1]["data"]["timeslots"] = []

        shape = classify_scheduler_add_subtree(config)

        self.assertEqual(shape["scheduler_add_entity_scalar_count"], 0)
        self.assertEqual(shape["scheduler_add_entity_id_key_count"], 0)
        self.assertEqual(shape["shape_reason"], "SCHEDULER_ADD_TARGET_NOT_IDENTIFIED")

    def test_missing_entity_id_key_with_single_scalar_is_classified(self) -> None:
        config = direct_target_config()
        action = config["script"]["heater_sched_save"]["sequence"][1]["data"][
            "timeslots"
        ][0]["actions"][0]
        action.pop("entity_id")
        action.pop("service")
        action["target_ref"] = "switch.private_fixture"

        shape = classify_scheduler_add_subtree(config)

        self.assertEqual(shape["scheduler_add_entity_id_key_count"], 0)
        self.assertEqual(shape["scheduler_add_entity_scalar_count"], 1)
        self.assertEqual(
            shape["shape_reason"],
            "UNIQUE_ENTITY_SCALAR_UNDER_SCHEDULER_ADD_NO_ENTITY_ID_KEY",
        )

    def test_ambiguous_entity_ids_are_classified(self) -> None:
        config = direct_target_config()
        actions = config["script"]["heater_sched_save"]["sequence"][1]["data"][
            "timeslots"
        ][0]["actions"]
        actions.append(
            {
                "entity_id": "switch.second_private_fixture",
                "service": "switch.turn_off",
            }
        )

        shape = classify_scheduler_add_subtree(config)

        self.assertEqual(shape["scheduler_add_entity_id_key_count"], 2)
        self.assertEqual(shape["scheduler_add_entity_id_scalar_count"], 2)
        self.assertEqual(
            shape["shape_reason"], "SCHEDULER_ADD_TARGET_STRUCTURALLY_AMBIGUOUS"
        )

    def test_modern_variable_based_candidate_has_no_direct_target_in_add(self) -> None:
        shape = classify_scheduler_add_subtree(variable_target_config())

        self.assertEqual(shape["scheduler_add_mapping_count"], 1)
        self.assertEqual(shape["scheduler_add_timeslots_string_count"], 1)
        self.assertEqual(shape["scheduler_add_entity_id_key_count"], 0)
        self.assertEqual(shape["scheduler_add_entity_scalar_count"], 0)
        self.assertEqual(shape["shape_reason"], "SCHEDULER_ADD_TARGET_NOT_IDENTIFIED")

    def test_report_is_sanitized(self) -> None:
        probe = {
            "runtime_error": False,
            "installed_home_assistant_yaml_loader_used": True,
            "home_assistant_secret_resolution_used": True,
            "shape": classify_scheduler_add_subtree(direct_target_config()),
        }

        report = build_report(probe, expected="2026.8.2", running="2026.8.2")
        encoded = json.dumps(report, sort_keys=True)

        self.assertEqual(report["decision"], DIAGNOSTIC_COMPLETE)
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(all(value is False for value in report["mutation"].values()))
        self.assertNotIn("switch.private_fixture", encoded)
        self.assertNotIn("switch.second_private_fixture", encoded)

    def test_version_mismatch_blocks(self) -> None:
        probe = {
            "runtime_error": False,
            "installed_home_assistant_yaml_loader_used": True,
            "home_assistant_secret_resolution_used": True,
            "shape": classify_scheduler_add_subtree(direct_target_config()),
        }

        report = build_report(probe, expected="2026.8.2", running="2026.8.3")

        self.assertEqual(report["decision"], BLOCKED)
        self.assertEqual(report["reasons"], ["HOME_ASSISTANT_VERSION_MISMATCH"])


if __name__ == "__main__":
    unittest.main()
