#!/usr/bin/env python3
"""Plan disabled legacy heater schedule transition without production mutation."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.audit_heater_scheduling_live import (
    collect_runtime_probe,
    expected_version,
    running_version,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONTAINER = "homeassistant"
READY = "READY_FOR_OWNER_CHOICE_PRESERVE_OR_RETIRE_LATENT_LEGACY_SCHEDULE"
BLOCKED = "BLOCKED"

SUPPLEMENTAL_PROBE = r'''
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path("/config")
LEGACY = ROOT / "packages" / "silditajs.yaml"
SCHED = ROOT / "packages" / "heater_scheduler.yaml"
STORAGE = ROOT / ".storage" / "scheduler.storage"
DB = ROOT / "home-assistant_v2.db"


def safe_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return None


def valid_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}:\d{2}", value):
        return False
    hh, mm, ss = (int(part) for part in value.split(":"))
    return 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59


def latest(conn, entity_id):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "states_meta" in tables:
        row = conn.execute(
            """
            SELECT s.state
            FROM states AS s
            JOIN states_meta AS sm ON sm.metadata_id = s.metadata_id
            WHERE sm.entity_id = ?
            ORDER BY s.state_id DESC
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        return row[0] if row else "unknown"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(states)")}
    if "entity_id" not in columns:
        return "unknown"
    row = conn.execute(
        "SELECT state FROM states WHERE entity_id = ? ORDER BY state_id DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    return row[0] if row else "unknown"

legacy_text = safe_text(LEGACY)
sched_text = safe_text(SCHED)

source = {
    "legacy_source_available": isinstance(legacy_text, str),
    "scheduler_source_available": isinstance(sched_text, str),
    "legacy_daily_on_semantics_exact": bool(
        isinstance(legacy_text, str)
        and legacy_text.count("id: silditajs_grafiks_on") == 1
        and legacy_text.count("at: input_datetime.silditajs_ieslegt") == 1
        and legacy_text.count("service: switch.turn_on") >= 1
    ),
    "legacy_daily_off_semantics_exact": bool(
        isinstance(legacy_text, str)
        and legacy_text.count("id: silditajs_grafiks_off") == 1
        and legacy_text.count("at: input_datetime.silditajs_izslegt") == 1
        and legacy_text.count("service: switch.turn_off") >= 2
    ),
    "legacy_gate_present": bool(
        isinstance(legacy_text, str)
        and legacy_text.count("entity_id: input_boolean.silditajs_grafiks") >= 2
    ),
    "timer_semantics_present": bool(
        isinstance(legacy_text, str)
        and legacy_text.count("id: silditajs_auto_off") == 1
    ),
    "scheduler_add_present": bool(
        isinstance(sched_text, str) and "service: scheduler.add" in sched_text
    ),
    "shared_binding_token_proven_without_secret_read": bool(
        isinstance(legacy_text, str)
        and isinstance(sched_text, str)
        and "!secret heater_switch_entity" in legacy_text
        and "!secret heater_switch_entity" in sched_text
    ),
}

states = {
    "recorder_available": False,
    "legacy_on_time_valid": False,
    "legacy_off_time_valid": False,
}
if DB.is_file():
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2.0)
        on_value = latest(conn, "input_datetime.silditajs_ieslegt")
        off_value = latest(conn, "input_datetime.silditajs_izslegt")
        states = {
            "recorder_available": True,
            "legacy_on_time_valid": valid_time(on_value),
            "legacy_off_time_valid": valid_time(off_value),
        }
        conn.close()
    except sqlite3.Error:
        pass

storage = {
    "storage_available": False,
    "valid_wrapper": False,
    "total_schedule_entry_count": -1,
}
try:
    payload = json.loads(STORAGE.read_text(encoding="utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    schedules = data.get("schedules") if isinstance(data, dict) else None
    if isinstance(schedules, list) and all(isinstance(item, dict) for item in schedules):
        storage = {
            "storage_available": True,
            "valid_wrapper": True,
            "total_schedule_entry_count": len(schedules),
        }
except (OSError, UnicodeError, json.JSONDecodeError):
    pass

print(json.dumps({
    "source": source,
    "states": states,
    "scheduler_storage": storage,
    "privacy": {
        "legacy_time_values_emitted": False,
        "entity_ids_or_targets_emitted": False,
        "secret_values_emitted": False,
        "raw_yaml_emitted": False,
        "raw_storage_emitted": False,
        "private_paths_emitted": False,
    },
}, sort_keys=True))
'''


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def collect_supplemental_probe(docker: str, container: str) -> dict[str, Any]:
    result = _run([docker, "exec", "-i", container, "python", "-"], input_text=SUPPLEMENTAL_PROBE)
    if result.returncode != 0:
        raise RuntimeError("supplemental heater transition probe failed")
    try:
        decoded = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("supplemental heater transition probe returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("supplemental heater transition probe returned unexpected data")
    return decoded


def candidate_summary(root: Path = ROOT) -> dict[str, bool]:
    legacy = (root / "packages" / "silditajs.yaml").read_text(encoding="utf-8")
    scheduler = (root / "packages" / "heater_scheduler.yaml").read_text(encoding="utf-8")
    return {
        "legacy_schedule_helper_removed": "silditajs_grafiks:" not in legacy,
        "legacy_time_helpers_removed": (
            "silditajs_ieslegt:" not in legacy and "silditajs_izslegt:" not in legacy
        ),
        "legacy_direct_automations_removed": (
            "id: silditajs_grafiks_on" not in legacy
            and "id: silditajs_grafiks_off" not in legacy
        ),
        "timer_preserved": "id: silditajs_auto_off" in legacy,
        "scheduler_save_script_present": (
            "heater_sched_save:" in scheduler and "service: scheduler.add" in scheduler
        ),
        "candidate_binding_token_present": "!secret heater_switch_entity" in scheduler,
    }


def build_report(
    base: dict[str, Any],
    supplemental: dict[str, Any],
    candidate: dict[str, bool],
    *,
    expected: str,
    running: str,
) -> dict[str, Any]:
    reasons: list[str] = []

    states = base.get("states") if isinstance(base.get("states"), dict) else {}
    dashboard = base.get("dashboard") if isinstance(base.get("dashboard"), dict) else {}
    privacy = base.get("privacy") if isinstance(base.get("privacy"), dict) else {}
    source = supplemental.get("source") if isinstance(supplemental.get("source"), dict) else {}
    time_states = supplemental.get("states") if isinstance(supplemental.get("states"), dict) else {}
    storage = supplemental.get("scheduler_storage") if isinstance(supplemental.get("scheduler_storage"), dict) else {}
    supplemental_privacy = supplemental.get("privacy") if isinstance(supplemental.get("privacy"), dict) else {}

    checks = {
        "HOME_ASSISTANT_VERSION_MISMATCH": running == expected,
        "LEGACY_SCHEDULE_HELPER_NOT_OFF": states.get("legacy_schedule_helper_state") == "off",
        "LEGACY_AUTOMATIONS_NOT_EXACT": states.get("legacy_automation_count") == 2,
        "LEGACY_ON_TIME_INVALID": time_states.get("legacy_on_time_valid") is True,
        "LEGACY_OFF_TIME_INVALID": time_states.get("legacy_off_time_valid") is True,
        "LEGACY_SOURCE_SEMANTICS_NOT_EXACT": all(
            source.get(key) is True
            for key in (
                "legacy_source_available",
                "scheduler_source_available",
                "legacy_daily_on_semantics_exact",
                "legacy_daily_off_semantics_exact",
                "legacy_gate_present",
                "timer_semantics_present",
                "scheduler_add_present",
                "shared_binding_token_proven_without_secret_read",
            )
        ),
        "SCHEDULER_STORAGE_NOT_EMPTY": (
            storage.get("storage_available") is True
            and storage.get("valid_wrapper") is True
            and storage.get("total_schedule_entry_count") == 0
        ),
        "LEGACY_DASHBOARD_REFERENCE_PRESENT": dashboard.get("legacy_schedule_reference_file_count") == 0,
        "NEW_DASHBOARD_REFERENCE_MISSING": dashboard.get("new_schedule_reference_file_count", 0) >= 1,
        "TIMER_DASHBOARD_REFERENCE_MISSING": dashboard.get("timer_reference_file_count", 0) >= 1,
        "CANDIDATE_SHAPE_INVALID": all(candidate.values()),
        "PRIVACY_GUARD_FAILED": (
            all(value is False for value in privacy.values())
            and all(value is False for value in supplemental_privacy.values())
        ),
    }
    reasons.extend(reason for reason, passed in checks.items() if not passed)
    decision = READY if not reasons else BLOCKED

    return {
        "schema": 1,
        "decision": decision,
        "reasons": reasons,
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": running == expected,
        },
        "current_behavior": {
            "recurring_schedule_active": False if states.get("legacy_schedule_helper_state") == "off" else None,
            "legacy_schedule_helper_off": states.get("legacy_schedule_helper_state") == "off",
            "legacy_automation_count": states.get("legacy_automation_count", 0),
            "legacy_automation_enabled_count": states.get("legacy_automation_enabled_count", 0),
            "latent_legacy_on_time_valid": time_states.get("legacy_on_time_valid") is True,
            "latent_legacy_off_time_valid": time_states.get("legacy_off_time_valid") is True,
            "scheduler_storage_empty": storage.get("total_schedule_entry_count") == 0,
        },
        "source_reconciliation": {
            "legacy_daily_on_semantics_exact": source.get("legacy_daily_on_semantics_exact") is True,
            "legacy_daily_off_semantics_exact": source.get("legacy_daily_off_semantics_exact") is True,
            "shared_binding_token_proven_without_secret_read": source.get("shared_binding_token_proven_without_secret_read") is True,
            **candidate,
        },
        "transition": {
            "no_bootstrap_preserves_current_active_off_behavior": decision == READY,
            "no_bootstrap_retires_latent_legacy_time_values": decision == READY,
            "scheduler_bootstrap_requires_separate_state_preserving_authorization": decision == READY,
            "scheduler_add_entries_default_enabled": True,
            "scheduler_add_service_has_enabled_argument": False,
        },
        "claims": {
            "owner_choice_made": False,
            "scheduler_bootstrap_authorized": False,
            "production_apply_authorized": False,
        },
        "privacy": {
            "legacy_time_values_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "secret_values_emitted": False,
            "raw_yaml_emitted": False,
            "raw_storage_emitted": False,
            "private_paths_emitted": False,
        },
        "mutation": {
            "scheduler_service_called": False,
            "helper_state_changed": False,
            "scheduler_storage_written": False,
            "home_assistant_config_written": False,
            "dashboard_written": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan disabled legacy heater schedule transition safely.")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--expected-version-file", type=Path, default=EXPECTED_VERSION_FILE)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": BLOCKED,
        "reasons": [reason],
        "claims": {
            "owner_choice_made": False,
            "scheduler_bootstrap_authorized": False,
            "production_apply_authorized": False,
        },
        "mutation": {
            "scheduler_service_called": False,
            "helper_state_changed": False,
            "scheduler_storage_written": False,
            "home_assistant_config_written": False,
            "dashboard_written": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.plan:
        report = blocked_report("PLANNER_GATE_REQUIRED")
    else:
        try:
            expected = expected_version(args.expected_version_file)
            running = running_version(args.docker, args.container)
            base = collect_runtime_probe(args.docker, args.container)
            supplemental = collect_supplemental_probe(args.docker, args.container)
            report = build_report(
                base,
                supplemental,
                candidate_summary(),
                expected=expected,
                running=running,
            )
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            report = blocked_report("TRANSITION_PLANNER_RUNTIME_ERROR")

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.stdout or True:
        sys.stdout.write(text)
    return 0 if report["decision"] == READY else 20


if __name__ == "__main__":
    raise SystemExit(main())
