import json
import unittest
from contextlib import redirect_stdout
from io import StringIO

from tools.materialize_heater_retire_candidate_privately import (
    MaterializationError,
    blocked_report,
    extract_live_target,
    main,
    materialize_candidate_texts,
    ready_report,
)


class HeaterRetireMaterializerTests(unittest.TestCase):
    def live_config(
        self,
        on_target: object = "switch.fixture_target",
        off_target: object = "switch.fixture_target",
        timer_target: object = "switch.fixture_target",
    ) -> dict:
        return {
            "automation": [
                {
                    "id": "silditajs_grafiks_on",
                    "action": [
                        {
                            "service": "switch.turn_on",
                            "target": {"entity_id": on_target},
                        }
                    ],
                },
                {
                    "id": "silditajs_grafiks_off",
                    "action": [
                        {
                            "service": "switch.turn_off",
                            "target": {"entity_id": off_target},
                        }
                    ],
                },
                {
                    "id": "silditajs_auto_off",
                    "action": [
                        {
                            "service": "switch.turn_off",
                            "target": {"entity_id": timer_target},
                        }
                    ],
                },
            ]
        }

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

    def test_extract_live_target_requires_all_three_equal(self) -> None:
        self.assertEqual(
            extract_live_target(self.live_config()),
            "switch.fixture_target",
        )
        self.assertIsNone(
            extract_live_target(
                self.live_config(off_target="switch.fixture_other")
            )
        )

    def test_extract_live_target_rejects_invalid_shape(self) -> None:
        self.assertIsNone(
            extract_live_target(self.live_config(timer_target="not-an-entity"))
        )
        self.assertIsNone(extract_live_target({"automation": []}))

    def test_materializes_private_binding_and_retire_initial(self) -> None:
        first, second, counts = materialize_candidate_texts(
            self.first_candidate(),
            self.second_candidate(),
            "switch.fixture_target",
        )

        self.assertNotIn("!secret", first)
        self.assertNotIn("!secret", second)
        self.assertEqual(first.count('"switch.fixture_target"'), 3)
        self.assertEqual(second.count('"switch.fixture_target"'), 1)
        self.assertIn('initial: "00:00:00"', second)
        self.assertEqual(counts["first_binding_placeholder_count"], 3)
        self.assertEqual(counts["second_binding_placeholder_count"], 1)
        self.assertEqual(counts["retire_initial_placeholder_count"], 1)
        self.assertEqual(counts["total_binding_placeholder_count"], 4)

    def test_first_candidate_secret_shape_change_fails_closed(self) -> None:
        first = self.first_candidate().replace(
            "entity_id: !secret fixture_target_a\n",
            "entity_id: switch.fixture_target\n",
            1,
        )
        with self.assertRaises(MaterializationError) as context:
            materialize_candidate_texts(
                first,
                self.second_candidate(),
                "switch.fixture_target",
            )
        self.assertEqual(
            context.exception.reason,
            "FIRST_CANDIDATE_SECRET_SHAPE_CHANGED",
        )

    def test_second_candidate_extra_secret_fails_closed(self) -> None:
        second = self.second_candidate() + "extra: !secret fixture_extra\n"
        with self.assertRaises(MaterializationError) as context:
            materialize_candidate_texts(
                self.first_candidate(),
                second,
                "switch.fixture_target",
            )
        self.assertEqual(
            context.exception.reason,
            "SECOND_CANDIDATE_SECRET_SHAPE_CHANGED",
        )

    def test_second_candidate_binding_shape_change_fails_closed(self) -> None:
        second = self.second_candidate().replace(
            "heater_entity: !secret fixture_target_b",
            "other_key: !secret fixture_target_b",
        )
        with self.assertRaises(MaterializationError) as context:
            materialize_candidate_texts(
                self.first_candidate(),
                second,
                "switch.fixture_target",
            )
        self.assertEqual(
            context.exception.reason,
            "SECOND_BINDING_PLACEHOLDER_SHAPE_CHANGED",
        )

    def test_second_candidate_initial_shape_change_fails_closed(self) -> None:
        second = self.second_candidate().replace(
            "initial: !secret fixture_initial",
            "other_initial: !secret fixture_initial",
        )
        with self.assertRaises(MaterializationError) as context:
            materialize_candidate_texts(
                self.first_candidate(),
                second,
                "switch.fixture_target",
            )
        self.assertEqual(
            context.exception.reason,
            "RETIRE_INITIAL_PLACEHOLDER_SHAPE_CHANGED",
        )

    def test_invalid_private_target_fails_closed(self) -> None:
        with self.assertRaises(MaterializationError) as context:
            materialize_candidate_texts(
                self.first_candidate(),
                self.second_candidate(),
                "private target value",
            )
        self.assertEqual(context.exception.reason, "PRIVATE_TARGET_SHAPE_INVALID")

    def test_ready_report_is_privacy_safe(self) -> None:
        report = ready_report(
            {
                "first_binding_placeholder_count": 3,
                "second_binding_placeholder_count": 1,
                "retire_initial_placeholder_count": 1,
                "total_binding_placeholder_count": 4,
            }
        )
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("switch.fixture_target", encoded)
        self.assertTrue(report["binding"]["legacy_targets_resolved_and_equal"])
        self.assertTrue(report["materialization"]["neutral_initial_used"])
        self.assertTrue(
            report["materialization"]["materialized_yaml_reload_passed"]
        )
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(
            all(value is False for value in report["production_mutation"].values())
        )

    def test_cli_without_gate_is_fail_closed(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            rc = main([])
        report = json.loads(output.getvalue())
        self.assertEqual(rc, 20)
        self.assertEqual(
            report,
            blocked_report("PRIVATE_RETIRE_MATERIALIZATION_GATE_REQUIRED"),
        )


if __name__ == "__main__":
    unittest.main()
