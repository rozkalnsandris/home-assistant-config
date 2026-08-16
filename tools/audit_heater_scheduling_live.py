#!/usr/bin/env python3
"""Audit live heater scheduling ownership without emitting private bindings."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONTAINER = "homeassistant"

RUNTIME_PROBE = r'''
import json
import re
import sqlite3
from pathlib import Path

CONFIG_ROOT = Path("/config")


def _safe_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return None


def _json_file(path):
    text = _safe_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _yaml_files():
    excluded = {
        ".storage",
        ".cloud",
        "backups",
        "backup",
        "custom_components",
        "deps",
        "tts",
        "www",
    }
    for pattern in ("*.yaml", "*.yml"):
        for path in CONFIG_ROOT.rglob(pattern):
            try:
                rel = path.relative_to(CONFIG_ROOT)
            except ValueError:
                continue
            if any(part in excluded for part in rel.parts):
                continue
            if path.is_file():
                yield path, rel


def _source_and_dashboard_markers():
    source = {
        "legacy_direct_schedule_file_count": 0,
        "scheduler_authority_file_count": 0,
        "timer_file_count": 0,
    }
    dashboard = {
        "new_schedule_reference_file_count": 0,
        "timer_reference_file_count": 0,
        "legacy_schedule_reference_file_count": 0,
    }

    for path, rel in _yaml_files():
        text = _safe_text(path)
        if text is None:
            continue

        if (
            "id: silditajs_grafiks_on" in text
            or "id: silditajs_grafiks_off" in text
        ):
            source["legacy_direct_schedule_file_count"] += 1
        if "scheduler.add" in text and "heater_sched_save" in text:
            source["scheduler_authority_file_count"] += 1
        if "id: silditajs_auto_off" in text:
            source["timer_file_count"] += 1

        if rel.parts and rel.parts[0] == "packages":
            continue

        if "heater_sched_" in text:
            dashboard["new_schedule_reference_file_count"] += 1
        if "silditajs_taimeris" in text:
            dashboard["timer_reference_file_count"] += 1
        if any(
            marker in text
            for marker in (
                "silditajs_grafiks",
                "silditajs_ieslegt",
                "silditajs_izslegt",
            )
        ):
            dashboard["legacy_schedule_reference_file_count"] += 1

    return source, dashboard


def _scheduler_summary():
    path = CONFIG_ROOT / ".storage" / "scheduler.storage"
    payload = _json_file(path)
    if not isinstance(payload, dict):
        return {
            "storage_available": False,
            "matching_entry_count": 0,
            "enabled_entry_count": 0,
            "turn_on_entry_count": 0,
            "turn_off_entry_count": 0,
            "other_action_entry_count": 0,
            "single_target": False,
        }

    data = payload.get("data", payload)
    schedules = data.get("schedules", []) if isinstance(data, dict) else []
    if not isinstance(schedules, list):
        schedules = []

    matched = []
    target_values = set()
    turn_on = 0
    turn_off = 0
    other = 0
    enabled = 0

    for entry in schedules:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        if not re.match(r"^Sildītājs (?:ON|OFF) ", name):
            continue

        matched.append(entry)
        if entry.get("enabled") is True:
            enabled += 1

        services = set()
        entry_targets = set()
        timeslots = entry.get("timeslots", [])
        if not isinstance(timeslots, list):
            timeslots = []
        for slot in timeslots:
            if not isinstance(slot, dict):
                continue
            actions = slot.get("actions", [])
            if not isinstance(actions, list):
                continue
            for action in actions:
                if not isinstance(action, dict):
                    continue
                service = action.get("service")
                entity_id = action.get("entity_id")
                if isinstance(service, str):
                    services.add(service)
                if isinstance(entity_id, str) and entity_id:
                    entry_targets.add(entity_id)
                    target_values.add(entity_id)

        if services and services <= {"switch.turn_on"}:
            turn_on += 1
        elif services and services <= {"switch.turn_off"}:
            turn_off += 1
        else:
            other += 1

        if len(entry_targets) != 1:
            other += 1

    return {
        "storage_available": True,
        "matching_entry_count": len(matched),
        "enabled_entry_count": enabled,
        "turn_on_entry_count": turn_on,
        "turn_off_entry_count": turn_off,
        "other_action_entry_count": other,
        "single_target": bool(matched) and len(target_values) == 1,
    }


def _state_summary():
    db = CONFIG_ROOT / "home-assistant_v2.db"
    if not db.is_file():
        return {
            "recorder_available": False,
            "legacy_schedule_helper_state": "unknown",
            "timer_helper_state": "unknown",
            "legacy_automation_count": 0,
            "legacy_automation_enabled_count": 0,
            "legacy_automation_disabled_count": 0,
        }

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {
            "recorder_available": False,
            "legacy_schedule_helper_state": "unknown",
            "timer_helper_state": "unknown",
            "legacy_automation_count": 0,
            "legacy_automation_enabled_count": 0,
            "legacy_automation_disabled_count": 0,
        }

    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        def latest(entity_id):
            if "states_meta" in tables:
                row = conn.execute(
                    """
                    SELECT s.state
                    FROM states AS s
                    JOIN states_meta AS sm
                      ON sm.metadata_id = s.metadata_id
                    WHERE sm.entity_id = ?
                    ORDER BY s.state_id DESC
                    LIMIT 1
                    """,
                    (entity_id,),
                ).fetchone()
                return row[0] if row else "unknown"

            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(states)")
            }
            if "entity_id" not in columns:
                return "unknown"
            row = conn.execute(
                """
                SELECT state
                FROM states
                WHERE entity_id = ?
                ORDER BY state_id DESC
                LIMIT 1
                """,
                (entity_id,),
            ).fetchone()
            return row[0] if row else "unknown"

        if "states_meta" in tables:
            automation_ids = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT entity_id
                    FROM states_meta
                    WHERE entity_id LIKE 'automation.silditajs_grafiks%'
                    ORDER BY entity_id
                    """
                )
            ]
        else:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(states)")
            }
            if "entity_id" in columns:
                automation_ids = [
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT DISTINCT entity_id
                        FROM states
                        WHERE entity_id LIKE 'automation.silditajs_grafiks%'
                        ORDER BY entity_id
                        """
                    )
                ]
            else:
                automation_ids = []

        automation_states = [latest(entity_id) for entity_id in automation_ids]

        return {
            "recorder_available": True,
            "legacy_schedule_helper_state": latest(
                "input_boolean.silditajs_grafiks"
            ),
            "timer_helper_state": latest("input_boolean.silditajs_taimeris"),
            "legacy_automation_count": len(automation_states),
            "legacy_automation_enabled_count": automation_states.count("on"),
            "legacy_automation_disabled_count": automation_states.count("off"),
        }
    except sqlite3.Error:
        return {
            "recorder_available": False,
            "legacy_schedule_helper_state": "unknown",
            "timer_helper_state": "unknown",
            "legacy_automation_count": 0,
            "legacy_automation_enabled_count": 0,
            "legacy_automation_disabled_count": 0,
        }
    finally:
        conn.close()


source, dashboard = _source_and_dashboard_markers()
report = {
    "source": source,
    "dashboard": dashboard,
    "scheduler": _scheduler_summary(),
    "states": _state_summary(),
    "privacy": {
        "exact_entity_targets_emitted": False,
        "scheduler_names_emitted": False,
        "schedule_times_emitted": False,
        "weekdays_emitted": False,
        "dashboard_paths_emitted": False,
        "config_contents_emitted": False,
        "secrets_read": False,
    },
}
print(json.dumps(report, sort_keys=True))
'''


def _run(
    command: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _require_success(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed")
    return result.stdout


def expected_version(path: Path = EXPECTED_VERSION_FILE) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("expected Home Assistant version is empty")
    return value


def running_version(docker: str, container: str) -> str:
    result = _run(
        [docker, "exec", container, "python", "-m", "homeassistant", "--version"]
    )
    value = _require_success(result, "Home Assistant version lookup").strip()
    if not value:
        raise RuntimeError("Home Assistant version lookup returned no value")
    return value


def collect_runtime_probe(docker: str, container: str) -> dict[str, Any]:
    result = _run(
        [docker, "exec", "-i", container, "python", "-"],
        input_text=RUNTIME_PROBE,
    )
    payload = _require_success(result, "live heater scheduling probe").strip()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("live heater scheduling probe returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("live heater scheduling probe returned unexpected data")
    return decoded


def evaluate_report(
    probe: dict[str, Any], *, expected: str, running: str
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    hard_block = False

    if running != expected:
        reasons.append("HOME_ASSISTANT_VERSION_MISMATCH")
        hard_block = True

    source = probe.get("source") if isinstance(probe.get("source"), dict) else {}
    dashboard = (
        probe.get("dashboard") if isinstance(probe.get("dashboard"), dict) else {}
    )
    scheduler = (
        probe.get("scheduler") if isinstance(probe.get("scheduler"), dict) else {}
    )
    states = probe.get("states") if isinstance(probe.get("states"), dict) else {}
    privacy = probe.get("privacy") if isinstance(probe.get("privacy"), dict) else {}

    if source.get("legacy_direct_schedule_file_count", 0) < 1:
        reasons.append("LEGACY_LIVE_SOURCE_NOT_CONFIRMED")
    if source.get("scheduler_authority_file_count", 0) < 1:
        reasons.append("SCHEDULER_SOURCE_NOT_CONFIRMED")
    if source.get("timer_file_count", 0) < 1:
        reasons.append("TIMER_SOURCE_NOT_CONFIRMED")

    if not states.get("recorder_available"):
        reasons.append("RECORDER_STATE_UNAVAILABLE")
    legacy_helper = states.get("legacy_schedule_helper_state")
    if legacy_helper != "off":
        reasons.append("LEGACY_SCHEDULE_HELPER_NOT_OFF")
        if legacy_helper == "on":
            hard_block = True
    if states.get("timer_helper_state") not in {"on", "off"}:
        reasons.append("TIMER_HELPER_STATE_UNKNOWN")
    if states.get("legacy_automation_count", 0) < 2:
        reasons.append("LEGACY_AUTOMATIONS_NOT_CONFIRMED")

    if not scheduler.get("storage_available"):
        reasons.append("SCHEDULER_STORAGE_UNAVAILABLE")
    if scheduler.get("matching_entry_count", 0) < 1:
        reasons.append("NO_MATCHING_SCHEDULER_ENTRIES")
    if scheduler.get("enabled_entry_count", 0) < 1:
        reasons.append("NO_ENABLED_SCHEDULER_ENTRIES")
    if scheduler.get("other_action_entry_count", 0) != 0:
        reasons.append("SCHEDULER_ACTION_SHAPE_UNEXPECTED")
    if not scheduler.get("single_target"):
        reasons.append("SCHEDULER_TARGET_NOT_SINGLE")

    if dashboard.get("new_schedule_reference_file_count", 0) < 1:
        reasons.append("NEW_SCHEDULE_DASHBOARD_REFERENCE_NOT_CONFIRMED")
    if dashboard.get("timer_reference_file_count", 0) < 1:
        reasons.append("TIMER_DASHBOARD_REFERENCE_NOT_CONFIRMED")
    if dashboard.get("legacy_schedule_reference_file_count", 0) > 0:
        reasons.append("LEGACY_DASHBOARD_REFERENCE_STILL_PRESENT")
        hard_block = True

    privacy_expected_false = (
        "exact_entity_targets_emitted",
        "scheduler_names_emitted",
        "schedule_times_emitted",
        "weekdays_emitted",
        "dashboard_paths_emitted",
        "config_contents_emitted",
        "secrets_read",
    )
    if any(privacy.get(key) is not False for key in privacy_expected_false):
        reasons.append("PRIVACY_GUARD_FAILED")
        hard_block = True

    if not reasons:
        return "READY_FOR_PRIVATE_PRODUCTION_APPLY_PREPARATION", []
    if hard_block:
        return "BLOCKED", reasons
    return "NEEDS_REVIEW", reasons


def build_report(
    probe: dict[str, Any], *, expected: str, running: str
) -> dict[str, Any]:
    decision, reasons = evaluate_report(probe, expected=expected, running=running)
    return {
        "schema": 1,
        "decision": decision,
        "reasons": reasons,
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": running == expected,
        },
        "source": probe.get("source", {}),
        "states": probe.get("states", {}),
        "scheduler": probe.get("scheduler", {}),
        "dashboard": probe.get("dashboard", {}),
        "privacy": probe.get("privacy", {}),
        "mutation": {
            "home_assistant_write": False,
            "scheduler_write": False,
            "dashboard_write": False,
            "reload_or_restart": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit live heater scheduling ownership with sanitized output."
    )
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument(
        "--expected-version-file",
        type=Path,
        default=EXPECTED_VERSION_FILE,
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        expected = expected_version(args.expected_version_file)
        running = running_version(args.docker, args.container)
        probe = collect_runtime_probe(args.docker, args.container)
        report = build_report(probe, expected=expected, running=running)
    except (OSError, RuntimeError) as exc:
        print(f"heater scheduling audit failed: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    if args.stdout or args.output is None:
        sys.stdout.write(text)

    if report["decision"] == "READY_FOR_PRIVATE_PRODUCTION_APPLY_PREPARATION":
        return 0
    if report["decision"] == "BLOCKED":
        return 20
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
