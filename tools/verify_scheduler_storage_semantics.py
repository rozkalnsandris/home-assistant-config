#!/usr/bin/env python3
"""Verify the empty Scheduler restart invariant without exposing private data."""

from __future__ import annotations

import argparse
import hmac
import json
import stat
import sys
from pathlib import Path
from typing import Any

MAX_STORAGE_BYTES = 4 * 1024 * 1024
MAX_SCHEDULES = 1000

PASS = "SCHEDULER_SEMANTIC_INVARIANT_PASS"
BLOCKED = "BLOCKED"


class SchedulerInvariantError(RuntimeError):
    """Fail-closed Scheduler storage validation error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def privacy_report() -> dict[str, bool]:
    return {
        "schedule_names_emitted": False,
        "schedule_ids_emitted": False,
        "entity_ids_or_targets_emitted": False,
        "schedule_times_emitted": False,
        "weekdays_or_dates_emitted": False,
        "service_data_emitted": False,
        "storage_paths_or_keys_emitted": False,
        "raw_storage_json_emitted": False,
        "hashes_emitted": False,
    }


def production_mutation_report() -> dict[str, bool]:
    return {
        "home_assistant_config_written": False,
        "scheduler_service_called": False,
        "scheduler_storage_written": False,
        "helper_state_changed": False,
        "heater_actuated": False,
        "reload_or_restart": False,
    }


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": BLOCKED,
        "reason": reason,
        "privacy": privacy_report(),
        "production_mutation": production_mutation_report(),
    }


def _load_storage(
    path: Path,
    *,
    label: str,
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SchedulerInvariantError(f"{label}_STORAGE_UNAVAILABLE") from exc

    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SchedulerInvariantError(f"{label}_STORAGE_NOT_REGULAR_FILE")
    if info.st_size > MAX_STORAGE_BYTES:
        raise SchedulerInvariantError(f"{label}_STORAGE_TOO_LARGE")

    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchedulerInvariantError(f"{label}_STORAGE_INVALID_JSON") from exc

    if not isinstance(payload, dict):
        raise SchedulerInvariantError(f"{label}_STORAGE_WRAPPER_INVALID")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SchedulerInvariantError(f"{label}_STORAGE_DATA_INVALID")
    schedules = data.get("schedules")
    if not isinstance(schedules, list):
        raise SchedulerInvariantError(f"{label}_STORAGE_SCHEDULES_INVALID")
    if len(schedules) > MAX_SCHEDULES:
        raise SchedulerInvariantError(f"{label}_STORAGE_SCHEDULE_LIMIT_EXCEEDED")
    if any(not isinstance(entry, dict) for entry in schedules):
        raise SchedulerInvariantError(f"{label}_STORAGE_ENTRY_INVALID")

    return raw, payload, schedules


def compare_scheduler_storage(before: Path, current: Path) -> dict[str, Any]:
    """Verify the RETIRE path's empty recurring-schedule invariant.

    Raw byte identity is evidence only across restart. A nonempty prewrite
    Scheduler baseline is deliberately unsupported here because Scheduler's
    persisted shutdown time influences post-restart initial action execution;
    that case needs a restart-aware verifier rather than schedules-only equality.
    """
    try:
        before_raw, before_payload, before_schedules = _load_storage(
            before, label="BEFORE"
        )
        current_raw, current_payload, current_schedules = _load_storage(
            current, label="CURRENT"
        )
    except SchedulerInvariantError as exc:
        return blocked_report(exc.reason)

    bytes_equal = (
        len(before_raw) == len(current_raw)
        and hmac.compare_digest(before_raw, current_raw)
    )
    parsed_json_equal = before_payload == current_payload
    schedules_equal = before_schedules == current_schedules

    if len(before_schedules) != 0:
        decision = BLOCKED
        reason = "NONEMPTY_PREWRITE_REQUIRES_RESTART_AWARE_VERIFICATION"
    elif not schedules_equal:
        decision = BLOCKED
        reason = "SCHEDULER_SCHEDULES_CHANGED"
    else:
        decision = PASS
        reason = "EMPTY_RECURRING_SCHEDULES_PRESERVED"

    return {
        "schema": 1,
        "decision": decision,
        "reason": reason,
        "scheduler": {
            "before_storage_valid": True,
            "current_storage_valid": True,
            "before_schedule_count": len(before_schedules),
            "current_schedule_count": len(current_schedules),
            "schedules_equal": schedules_equal,
            "parsed_json_equal": parsed_json_equal,
            "bytes_equal": bytes_equal,
            "raw_byte_identity_required_for_pass": False,
            "nonempty_prewrite_supported": False,
        },
        "privacy": privacy_report(),
        "production_mutation": production_mutation_report(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = compare_scheduler_storage(args.before, args.current)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("decision") == PASS else 20


if __name__ == "__main__":
    sys.exit(main())
