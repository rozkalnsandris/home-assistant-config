import json
from pathlib import Path
import tempfile
import unittest

from tools.verify_scheduler_storage_semantics import (
    BLOCKED,
    PASS,
    compare_scheduler_storage,
)


class SchedulerStorageSemanticInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.before = self.root / "before.json"
        self.current = self.root / "current.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, path: Path, payload: object, *, compact: bool = False) -> None:
        if compact:
            text = json.dumps(payload, separators=(",", ":"))
        else:
            text = json.dumps(payload, indent=2, sort_keys=True)
        path.write_text(text, encoding="utf-8")

    def test_exact_empty_snapshot_passes(self) -> None:
        payload = {
            "version": 3,
            "key": "scheduler.storage",
            "data": {"schedules": [], "tags": []},
        }
        self.write(self.before, payload)
        self.write(self.current, payload)

        report = compare_scheduler_storage(self.before, self.current)

        self.assertEqual(report["decision"], PASS)
        self.assertEqual(report["reason"], "EMPTY_RECURRING_SCHEDULES_PRESERVED")
        self.assertTrue(report["scheduler"]["schedules_equal"])
        self.assertTrue(report["scheduler"]["parsed_json_equal"])
        self.assertTrue(report["scheduler"]["bytes_equal"])

    def test_byte_drift_with_same_empty_schedules_passes(self) -> None:
        before = {
            "version": 3,
            "key": "scheduler.storage",
            "data": {"schedules": [], "tags": []},
        }
        current = {
            "version": 3,
            "minor_version": 1,
            "key": "scheduler.storage",
            "data": {"tags": [], "schedules": []},
        }
        self.write(self.before, before, compact=True)
        self.write(self.current, current)

        report = compare_scheduler_storage(self.before, self.current)

        self.assertEqual(report["decision"], PASS)
        self.assertEqual(report["reason"], "EMPTY_RECURRING_SCHEDULES_PRESERVED")
        self.assertTrue(report["scheduler"]["schedules_equal"])
        self.assertFalse(report["scheduler"]["parsed_json_equal"])
        self.assertFalse(report["scheduler"]["bytes_equal"])
        self.assertFalse(report["scheduler"]["raw_byte_identity_required_for_pass"])

    def test_byte_drift_with_same_nonempty_schedules_passes(self) -> None:
        schedules = [
            {
                "schedule_id": "fixture",
                "weekdays": ["mon"],
                "timeslots": [],
                "enabled": True,
            }
        ]
        before = {
            "version": 3,
            "key": "scheduler.storage",
            "data": {"schedules": schedules, "tags": []},
        }
        current = {
            "version": 3,
            "minor_version": 1,
            "key": "scheduler.storage",
            "data": {"tags": [], "schedules": schedules},
        }
        self.write(self.before, before, compact=True)
        self.write(self.current, current)

        report = compare_scheduler_storage(self.before, self.current)

        self.assertEqual(report["decision"], PASS)
        self.assertEqual(
            report["reason"], "RECURRING_SCHEDULES_EXACTLY_PRESERVED"
        )
        self.assertTrue(report["scheduler"]["schedules_equal"])
        self.assertFalse(report["scheduler"]["bytes_equal"])

    def test_empty_before_nonempty_current_blocks(self) -> None:
        self.write(self.before, {"data": {"schedules": []}})
        self.write(
            self.current,
            {"data": {"schedules": [{"schedule_id": "fixture"}]}},
        )

        report = compare_scheduler_storage(self.before, self.current)

        self.assertEqual(report["decision"], BLOCKED)
        self.assertEqual(report["reason"], "SCHEDULER_SCHEDULES_CHANGED")
        self.assertFalse(report["scheduler"]["schedules_equal"])

    def test_changed_nonempty_schedule_blocks(self) -> None:
        self.write(
            self.before,
            {"data": {"schedules": [{"schedule_id": "one", "enabled": True}]}},
        )
        self.write(
            self.current,
            {"data": {"schedules": [{"schedule_id": "one", "enabled": False}]}},
        )

        report = compare_scheduler_storage(self.before, self.current)

        self.assertEqual(report["decision"], BLOCKED)
        self.assertEqual(report["reason"], "SCHEDULER_SCHEDULES_CHANGED")

    def test_invalid_current_storage_fails_closed(self) -> None:
        self.write(self.before, {"data": {"schedules": []}})
        self.current.write_text("{", encoding="utf-8")

        report = compare_scheduler_storage(self.before, self.current)

        self.assertEqual(report["decision"], BLOCKED)
        self.assertEqual(report["reason"], "CURRENT_STORAGE_INVALID_JSON")
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(
            all(value is False for value in report["production_mutation"].values())
        )


if __name__ == "__main__":
    unittest.main()
