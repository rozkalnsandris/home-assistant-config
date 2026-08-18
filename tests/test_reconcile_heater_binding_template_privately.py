import json
import unittest

from tools.reconcile_heater_binding_privately import blocked_report
from tools.reconcile_heater_binding_template_privately import (
    main,
    scheduler_target_from_config,
)


class SchedulerTemplateBindingTests(unittest.TestCase):
    def variable_config(self) -> dict:
        return {
            "script": {
                "heater_sched_save": {
                    "sequence": [
                        {"variables": {"heater_entity": "switch.fixture_target"}},
                        {
                            "service": "scheduler.add",
                            "data": {"timeslots": "{{ fixture }}"},
                        },
                    ]
                }
            }
        }

    def template_config(self, timeslots: str) -> dict:
        return {
            "script": {
                "heater_sched_save": {
                    "sequence": [
                        {"variables": {"other": "not-an-entity"}},
                        {
                            "service": "scheduler.add",
                            "data": {"timeslots": timeslots},
                        },
                    ]
                }
            }
        }

    def test_existing_variable_path_is_preserved(self) -> None:
        self.assertEqual(
            scheduler_target_from_config(self.variable_config()),
            "switch.fixture_target",
        )

    def test_unique_quoted_entity_id_literal_is_extracted(self) -> None:
        config = self.template_config(
            "{{ [{'start': '00:00', 'actions': [{'entity_id': 'switch.fixture_target', 'service': 'switch.turn_on'}]}] }}"
        )
        self.assertEqual(
            scheduler_target_from_config(config),
            "switch.fixture_target",
        )

    def test_double_quoted_entity_id_literal_is_extracted(self) -> None:
        config = self.template_config(
            '{{ [{"actions": [{"entity_id": "switch.fixture_target", "service": "switch.turn_off"}]}] }}'
        )
        self.assertEqual(
            scheduler_target_from_config(config),
            "switch.fixture_target",
        )

    def test_service_literals_are_not_target_candidates(self) -> None:
        config = self.template_config(
            "{{ [{'service': 'switch.turn_on'}, {'service': 'switch.turn_off'}] }}"
        )
        self.assertIsNone(scheduler_target_from_config(config))

    def test_missing_entity_id_literal_stays_unresolved(self) -> None:
        config = self.template_config("{{ [{'start': '00:00'}] }}")
        self.assertIsNone(scheduler_target_from_config(config))

    def test_unquoted_entity_id_value_stays_unresolved(self) -> None:
        config = self.template_config(
            "{{ [{'entity_id': heater_entity, 'service': 'switch.turn_on'}] }}"
        )
        self.assertIsNone(scheduler_target_from_config(config))

    def test_ambiguous_entity_id_literals_stay_unresolved(self) -> None:
        config = self.template_config(
            "{{ [{'entity_id': 'switch.fixture_one'}, {'entity_id': 'switch.fixture_two'}] }}"
        )
        self.assertIsNone(scheduler_target_from_config(config))

    def test_multiple_scheduler_add_steps_stay_unresolved(self) -> None:
        config = self.template_config(
            "{{ [{'entity_id': 'switch.fixture_target'}] }}"
        )
        config["script"]["heater_sched_save"]["sequence"].append(
            {
                "service": "scheduler.add",
                "data": {
                    "timeslots": "{{ [{'entity_id': 'switch.fixture_target'}] }}"
                },
            }
        )
        self.assertIsNone(scheduler_target_from_config(config))

    def test_ambiguous_variable_path_does_not_fall_back(self) -> None:
        config = self.variable_config()
        config["script"]["heater_sched_save"]["sequence"].insert(
            1, {"variables": {"heater_entity": "switch.fixture_second"}}
        )
        self.assertIsNone(scheduler_target_from_config(config))

    def test_cli_without_reconcile_is_fail_closed(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            rc = main([])
        report = json.loads(output.getvalue())
        self.assertEqual(rc, 20)
        self.assertEqual(report, blocked_report("RECONCILIATION_GATE_REQUIRED"))


if __name__ == "__main__":
    unittest.main()
