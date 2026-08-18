#!/usr/bin/env python3
"""Diagnose Scheduler storage shape without emitting private schedule data."""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONFIG_ROOT = Path("/config")
MAX_STORAGE_BYTES = 4 * 1024 * 1024
MAX_SCHEDULES = 1000

EMPTY_DECISION = "SCHEDULER_STORAGE_EMPTY"
PRESENT_DECISION = "SCHEDULER_ENTRIES_PRESENT_REQUIRING_PRIVATE_TARGET_CORRELATION"
BLOCKED_DECISION = "BLOCKED"


class DiagnosticError(RuntimeError):
    """Fail-closed diagnostic error with a public-safe reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def expected_version(path: Path = EXPECTED_VERSION_FILE) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DiagnosticError("EXPECTED_VERSION_UNAVAILABLE") from exc
    if not value:
        raise DiagnosticError("EXPECTED_VERSION_EMPTY")
    return value


def running_version() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "homeassistant", "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise DiagnosticError("RUNNING_VERSION_UNAVAILABLE")
    value = result.stdout.strip()
    if not value:
        raise DiagnosticError("RUNNING_VERSION_EMPTY")
    return value


def _load_scheduler_entries(config_root: Path) -> tuple[list[dict[str, Any]], int]:
    storage_path = config_root / ".storage" / "scheduler.storage"
    try:
        info = storage_path.lstat()
    except FileNotFoundError as exc:
        raise DiagnosticError("SCHEDULER_STORAGE_UNAVAILABLE") from exc
    except OSError as exc:
        raise DiagnosticError("SCHEDULER_STORAGE_STAT_FAILED") from exc

    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise DiagnosticError("SCHEDULER_STORAGE_NOT_REGULAR_FILE")
    if info.st_size > MAX_STORAGE_BYTES:
        raise DiagnosticError("SCHEDULER_STORAGE_TOO_LARGE")

    try:
        raw = storage_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError("SCHEDULER_STORAGE_INVALID_JSON") from exc

    if not isinstance(payload, dict):
        raise DiagnosticError("SCHEDULER_STORAGE_WRAPPER_INVALID")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DiagnosticError("SCHEDULER_STORAGE_DATA_INVALID")
    schedules = data.get("schedules")
    if not isinstance(schedules, list):
        raise DiagnosticError("SCHEDULER_STORAGE_SCHEDULES_INVALID")
    if len(schedules) > MAX_SCHEDULES:
        raise DiagnosticError("SCHEDULER_STORAGE_SCHEDULE_LIMIT_EXCEEDED")
    if any(not isinstance(entry, dict) for entry in schedules):
        raise DiagnosticError("SCHEDULER_STORAGE_ENTRY_INVALID")
    return schedules, info.st_size


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    enabled_count = 0
    zero_target_count = 0
    single_target_count = 0
    multiple_target_count = 0
    turn_on_only_count = 0
    turn_off_only_count = 0
    mixed_or_other_count = 0
    malformed_count = 0

    target_schedule_counts: Counter[str] = Counter()
    target_classes: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        if entry.get("enabled") is True:
            enabled_count += 1

        timeslots = entry.get("timeslots")
        if not isinstance(timeslots, list):
            malformed_count += 1
            continue

        services: set[str] = set()
        targets: set[str] = set()
        entry_malformed = False

        for slot in timeslots:
            if not isinstance(slot, dict):
                entry_malformed = True
                break
            actions = slot.get("actions")
            if not isinstance(actions, list):
                entry_malformed = True
                break
            for action in actions:
                if not isinstance(action, dict):
                    entry_malformed = True
                    break
                service = action.get("service")
                entity_id = action.get("entity_id")
                if not isinstance(service, str) or not service:
                    entry_malformed = True
                    break
                services.add(service)
                if isinstance(entity_id, str) and entity_id:
                    targets.add(entity_id)
                elif entity_id is not None:
                    entry_malformed = True
                    break
            if entry_malformed:
                break

        if entry_malformed:
            malformed_count += 1
            continue

        if services and services <= {"switch.turn_on"}:
            action_class = "turn_on_only"
            turn_on_only_count += 1
        elif services and services <= {"switch.turn_off"}:
            action_class = "turn_off_only"
            turn_off_only_count += 1
        else:
            action_class = "mixed_or_other"
            mixed_or_other_count += 1

        if not targets:
            zero_target_count += 1
        elif len(targets) == 1:
            single_target_count += 1
            target = next(iter(targets))
            target_schedule_counts[target] += 1
            target_classes[target].add(action_class)
        else:
            multiple_target_count += 1
            for target in targets:
                target_schedule_counts[target] += 1
                target_classes[target].add(action_class)

    shared_targets = {
        target: count for target, count in target_schedule_counts.items() if count > 1
    }
    schedules_on_shared_targets = sum(shared_targets.values())
    max_schedules_per_target = max(target_schedule_counts.values(), default=0)
    on_off_pair_present = any(
        {"turn_on_only", "turn_off_only"} <= classes
        for classes in target_classes.values()
    )

    return {
        "total_schedule_entry_count": len(entries),
        "enabled_entry_count": enabled_count,
        "zero_action_target_entry_count": zero_target_count,
        "single_action_target_entry_count": single_target_count,
        "multiple_action_target_entry_count": multiple_target_count,
        "turn_on_only_entry_count": turn_on_only_count,
        "turn_off_only_entry_count": turn_off_only_count,
        "mixed_or_other_action_entry_count": mixed_or_other_count,
        "malformed_or_unsupported_entry_count": malformed_count,
        "distinct_private_target_count": len(target_schedule_counts),
        "private_targets_shared_by_multiple_schedules_count": len(shared_targets),
        "schedules_on_shared_private_targets_count": schedules_on_shared_targets,
        "max_schedule_count_for_single_private_target": max_schedules_per_target,
        "private_target_with_on_and_off_schedule_pair_present": on_off_pair_present,
    }


def build_report(
    *,
    entries: list[dict[str, Any]],
    storage_bytes: int,
    expected: str,
    running: str,
) -> dict[str, Any]:
    summary = summarize_entries(entries)
    reasons: list[str] = []

    if running != expected:
        decision = BLOCKED_DECISION
        reasons.append("HOME_ASSISTANT_VERSION_MISMATCH")
    elif summary["malformed_or_unsupported_entry_count"] != 0:
        decision = BLOCKED_DECISION
        reasons.append("SCHEDULER_ENTRY_SHAPE_UNSUPPORTED")
    elif summary["total_schedule_entry_count"] == 0:
        decision = EMPTY_DECISION
    else:
        decision = PRESENT_DECISION

    return {
        "schema": 1,
        "decision": decision,
        "reasons": reasons,
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": running == expected,
        },
        "scheduler_storage": {
            "storage_available": True,
            "valid_wrapper": True,
            "storage_bytes": storage_bytes,
            **summary,
        },
        "privacy": {
            "schedule_names_emitted": False,
            "schedule_ids_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "schedule_times_emitted": False,
            "weekdays_or_dates_emitted": False,
            "service_data_emitted": False,
            "storage_paths_or_keys_emitted": False,
            "raw_storage_json_emitted": False,
            "dashboard_content_emitted": False,
            "secrets_read": False,
        },
        "claims": {
            "heater_target_identified": False,
            "scheduler_authority_proven": False,
            "production_apply_authorized": False,
        },
        "mutation": {
            "scheduler_service_called": False,
            "scheduler_storage_written": False,
            "scheduler_storage_reloaded": False,
            "home_assistant_config_written": False,
            "dashboard_written": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def blocked_report(reason: str, *, expected: str | None = None) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": BLOCKED_DECISION,
        "reasons": [reason],
        "home_assistant": {
            "expected_version": expected,
            "running_version": None,
            "version_match": False,
        },
        "privacy": {
            "schedule_names_emitted": False,
            "schedule_ids_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "schedule_times_emitted": False,
            "weekdays_or_dates_emitted": False,
            "service_data_emitted": False,
            "storage_paths_or_keys_emitted": False,
            "raw_storage_json_emitted": False,
            "dashboard_content_emitted": False,
            "secrets_read": False,
        },
        "claims": {
            "heater_target_identified": False,
            "scheduler_authority_proven": False,
            "production_apply_authorized": False,
        },
        "mutation": {
            "scheduler_service_called": False,
            "scheduler_storage_written": False,
            "scheduler_storage_reloaded": False,
            "home_assistant_config_written": False,
            "dashboard_written": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose Scheduler storage shape with private-safe aggregate output."
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument(
        "--expected-version-file", type=Path, default=EXPECTED_VERSION_FILE
    )
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.diagnose:
        report = blocked_report("DIAGNOSTIC_GATE_REQUIRED")
    else:
        try:
            expected = expected_version(args.expected_version_file)
            running = running_version()
            entries, storage_bytes = _load_scheduler_entries(args.config_root)
            report = build_report(
                entries=entries,
                storage_bytes=storage_bytes,
                expected=expected,
                running=running,
            )
        except DiagnosticError as exc:
            report = blocked_report(exc.reason)

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.stdout or True:
        sys.stdout.write(text)

    if report["decision"] in {EMPTY_DECISION, PRESENT_DECISION}:
        return 0
    return 20


if __name__ == "__main__":
    raise SystemExit(main())
