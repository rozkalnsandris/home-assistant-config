#!/usr/bin/env python3
"""Diagnose Scheduler-add subtree shape without emitting private values."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.plan_heater_disabled_legacy_transition import expected_version, running_version

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONTAINER = "homeassistant"
DIAGNOSTIC_COMPLETE = "SCHEDULER_ADD_SUBTREE_DIAGNOSTIC_COMPLETE"
BLOCKED = "BLOCKED"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def _entity_scalar(value: Any) -> bool:
    return isinstance(value, str) and ENTITY_ID.fullmatch(str(value)) is not None


def _count_entity_scalars(value: Any) -> int:
    if _entity_scalar(value):
        return 1
    if isinstance(value, dict):
        return sum(_count_entity_scalars(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_entity_scalars(item) for item in value)
    return 0


def _scheduler_add_steps(value: Any) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("service") == "scheduler.add" or value.get("action") == "scheduler.add":
            matches.append(value)
        for item in value.values():
            matches.extend(_scheduler_add_steps(item))
    elif isinstance(value, list):
        for item in value:
            matches.extend(_scheduler_add_steps(item))
    return matches


def _entity_id_counts(value: Any) -> tuple[int, int]:
    key_count = 0
    scalar_count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "entity_id":
                key_count += 1
                if _entity_scalar(item):
                    scalar_count += 1
            nested_keys, nested_scalars = _entity_id_counts(item)
            key_count += nested_keys
            scalar_count += nested_scalars
    elif isinstance(value, list):
        for item in value:
            nested_keys, nested_scalars = _entity_id_counts(item)
            key_count += nested_keys
            scalar_count += nested_scalars
    return key_count, scalar_count


def _action_container_counts(value: Any) -> tuple[int, int, int, int]:
    key_count = 0
    list_count = 0
    mapping_entry_count = 0
    entity_scalar_count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "actions":
                key_count += 1
                if isinstance(item, list):
                    list_count += 1
                    mapping_entry_count += sum(isinstance(entry, dict) for entry in item)
                    entity_scalar_count += _count_entity_scalars(item)
            nested = _action_container_counts(item)
            key_count += nested[0]
            list_count += nested[1]
            mapping_entry_count += nested[2]
            entity_scalar_count += nested[3]
    elif isinstance(value, list):
        for item in value:
            nested = _action_container_counts(item)
            key_count += nested[0]
            list_count += nested[1]
            mapping_entry_count += nested[2]
            entity_scalar_count += nested[3]
    return key_count, list_count, mapping_entry_count, entity_scalar_count


def classify_scheduler_add_subtree(config: Any) -> dict[str, Any]:
    top_level_mapping = isinstance(config, dict)
    scripts = config.get("script") if top_level_mapping else None
    script_mapping_present = isinstance(scripts, dict)
    save_present = bool(script_mapping_present and "heater_sched_save" in scripts)
    save_script = scripts.get("heater_sched_save") if script_mapping_present else None
    save_mapping = isinstance(save_script, dict)
    sequence = save_script.get("sequence") if save_mapping else None
    sequence_is_list = isinstance(sequence, list)

    add_steps = _scheduler_add_steps(save_script) if save_mapping else []
    add_count = len(add_steps)

    data_mapping_count = 0
    data_entity_scalar_count = 0
    timeslots_key_count = 0
    timeslots_list_count = 0
    timeslots_string_count = 0
    timeslots_entity_scalar_count = 0
    actions_key_count = 0
    actions_list_count = 0
    action_entry_mapping_count = 0
    actions_entity_scalar_count = 0
    entity_id_key_count = 0
    entity_id_scalar_count = 0
    add_entity_scalar_count = 0

    for step in add_steps:
        add_entity_scalar_count += _count_entity_scalars(step)
        data = step.get("data")
        if isinstance(data, dict):
            data_mapping_count += 1
            data_entity_scalar_count += _count_entity_scalars(data)
            if "timeslots" in data:
                timeslots_key_count += 1
                timeslots = data.get("timeslots")
                if isinstance(timeslots, list):
                    timeslots_list_count += 1
                if isinstance(timeslots, str):
                    timeslots_string_count += 1
                timeslots_entity_scalar_count += _count_entity_scalars(timeslots)

        action_counts = _action_container_counts(step)
        actions_key_count += action_counts[0]
        actions_list_count += action_counts[1]
        action_entry_mapping_count += action_counts[2]
        actions_entity_scalar_count += action_counts[3]

        id_counts = _entity_id_counts(step)
        entity_id_key_count += id_counts[0]
        entity_id_scalar_count += id_counts[1]

    if not top_level_mapping:
        reason = "TOP_LEVEL_NOT_MAPPING"
    elif not script_mapping_present:
        reason = "SCRIPT_MAPPING_MISSING"
    elif not save_present:
        reason = "EXPECTED_SAVE_SCRIPT_MISSING"
    elif not save_mapping:
        reason = "EXPECTED_SAVE_SCRIPT_NOT_MAPPING"
    elif not sequence_is_list:
        reason = "EXPECTED_SEQUENCE_NOT_LIST"
    elif add_count != 1:
        reason = "SCHEDULER_ADD_STEP_NOT_EXACT"
    elif entity_id_scalar_count == 1:
        reason = "UNIQUE_ENTITY_ID_SCALAR_UNDER_SCHEDULER_ADD"
    elif entity_id_key_count > 0 and entity_id_scalar_count == 0:
        reason = "ENTITY_ID_VALUE_SHAPE_INVALID"
    elif add_entity_scalar_count == 1:
        reason = "UNIQUE_ENTITY_SCALAR_UNDER_SCHEDULER_ADD_NO_ENTITY_ID_KEY"
    elif add_entity_scalar_count > 1:
        reason = "SCHEDULER_ADD_TARGET_STRUCTURALLY_AMBIGUOUS"
    else:
        reason = "SCHEDULER_ADD_TARGET_NOT_IDENTIFIED"

    return {
        "top_level_mapping": top_level_mapping,
        "script_mapping_present": script_mapping_present,
        "expected_save_script_present": save_present,
        "expected_save_script_mapping": save_mapping,
        "expected_sequence_is_list": sequence_is_list,
        "scheduler_add_mapping_count": add_count,
        "scheduler_add_data_mapping_count": data_mapping_count,
        "scheduler_add_data_entity_scalar_count": data_entity_scalar_count,
        "scheduler_add_timeslots_key_count": timeslots_key_count,
        "scheduler_add_timeslots_list_count": timeslots_list_count,
        "scheduler_add_timeslots_string_count": timeslots_string_count,
        "scheduler_add_timeslots_entity_scalar_count": timeslots_entity_scalar_count,
        "scheduler_add_actions_key_count": actions_key_count,
        "scheduler_add_actions_list_count": actions_list_count,
        "scheduler_add_action_entry_mapping_count": action_entry_mapping_count,
        "scheduler_add_actions_entity_scalar_count": actions_entity_scalar_count,
        "scheduler_add_entity_id_key_count": entity_id_key_count,
        "scheduler_add_entity_id_scalar_count": entity_id_scalar_count,
        "scheduler_add_entity_scalar_count": add_entity_scalar_count,
        "shape_reason": reason,
    }


SUBTREE_PROBE = r'''
import json
import re
from pathlib import Path

from homeassistant.util.yaml import Secrets, load_yaml_dict

ROOT = Path("/config")
SCHEDULER = ROOT / "packages" / "heater_scheduler.yaml"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def entity_scalar(value):
    return isinstance(value, str) and ENTITY_ID.fullmatch(str(value)) is not None


def count_entity_scalars(value):
    if entity_scalar(value):
        return 1
    if isinstance(value, dict):
        return sum(count_entity_scalars(item) for item in value.values())
    if isinstance(value, list):
        return sum(count_entity_scalars(item) for item in value)
    return 0


def scheduler_add_steps(value):
    matches = []
    if isinstance(value, dict):
        if value.get("service") == "scheduler.add" or value.get("action") == "scheduler.add":
            matches.append(value)
        for item in value.values():
            matches.extend(scheduler_add_steps(item))
    elif isinstance(value, list):
        for item in value:
            matches.extend(scheduler_add_steps(item))
    return matches


def entity_id_counts(value):
    key_count = 0
    scalar_count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "entity_id":
                key_count += 1
                if entity_scalar(item):
                    scalar_count += 1
            nested_keys, nested_scalars = entity_id_counts(item)
            key_count += nested_keys
            scalar_count += nested_scalars
    elif isinstance(value, list):
        for item in value:
            nested_keys, nested_scalars = entity_id_counts(item)
            key_count += nested_keys
            scalar_count += nested_scalars
    return key_count, scalar_count


def action_container_counts(value):
    key_count = 0
    list_count = 0
    mapping_entry_count = 0
    entity_scalar_count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "actions":
                key_count += 1
                if isinstance(item, list):
                    list_count += 1
                    mapping_entry_count += sum(isinstance(entry, dict) for entry in item)
                    entity_scalar_count += count_entity_scalars(item)
            nested = action_container_counts(item)
            key_count += nested[0]
            list_count += nested[1]
            mapping_entry_count += nested[2]
            entity_scalar_count += nested[3]
    elif isinstance(value, list):
        for item in value:
            nested = action_container_counts(item)
            key_count += nested[0]
            list_count += nested[1]
            mapping_entry_count += nested[2]
            entity_scalar_count += nested[3]
    return key_count, list_count, mapping_entry_count, entity_scalar_count


def classify(config):
    top_level_mapping = isinstance(config, dict)
    scripts = config.get("script") if top_level_mapping else None
    script_mapping_present = isinstance(scripts, dict)
    save_present = bool(script_mapping_present and "heater_sched_save" in scripts)
    save_script = scripts.get("heater_sched_save") if script_mapping_present else None
    save_mapping = isinstance(save_script, dict)
    sequence = save_script.get("sequence") if save_mapping else None
    sequence_is_list = isinstance(sequence, list)

    add_steps = scheduler_add_steps(save_script) if save_mapping else []
    add_count = len(add_steps)

    data_mapping_count = 0
    data_entity_scalar_count = 0
    timeslots_key_count = 0
    timeslots_list_count = 0
    timeslots_string_count = 0
    timeslots_entity_scalar_count = 0
    actions_key_count = 0
    actions_list_count = 0
    action_entry_mapping_count = 0
    actions_entity_scalar_count = 0
    entity_id_key_count = 0
    entity_id_scalar_count = 0
    add_entity_scalar_count = 0

    for step in add_steps:
        add_entity_scalar_count += count_entity_scalars(step)
        data = step.get("data")
        if isinstance(data, dict):
            data_mapping_count += 1
            data_entity_scalar_count += count_entity_scalars(data)
            if "timeslots" in data:
                timeslots_key_count += 1
                timeslots = data.get("timeslots")
                if isinstance(timeslots, list):
                    timeslots_list_count += 1
                if isinstance(timeslots, str):
                    timeslots_string_count += 1
                timeslots_entity_scalar_count += count_entity_scalars(timeslots)

        action_counts = action_container_counts(step)
        actions_key_count += action_counts[0]
        actions_list_count += action_counts[1]
        action_entry_mapping_count += action_counts[2]
        actions_entity_scalar_count += action_counts[3]

        id_counts = entity_id_counts(step)
        entity_id_key_count += id_counts[0]
        entity_id_scalar_count += id_counts[1]

    if not top_level_mapping:
        reason = "TOP_LEVEL_NOT_MAPPING"
    elif not script_mapping_present:
        reason = "SCRIPT_MAPPING_MISSING"
    elif not save_present:
        reason = "EXPECTED_SAVE_SCRIPT_MISSING"
    elif not save_mapping:
        reason = "EXPECTED_SAVE_SCRIPT_NOT_MAPPING"
    elif not sequence_is_list:
        reason = "EXPECTED_SEQUENCE_NOT_LIST"
    elif add_count != 1:
        reason = "SCHEDULER_ADD_STEP_NOT_EXACT"
    elif entity_id_scalar_count == 1:
        reason = "UNIQUE_ENTITY_ID_SCALAR_UNDER_SCHEDULER_ADD"
    elif entity_id_key_count > 0 and entity_id_scalar_count == 0:
        reason = "ENTITY_ID_VALUE_SHAPE_INVALID"
    elif add_entity_scalar_count == 1:
        reason = "UNIQUE_ENTITY_SCALAR_UNDER_SCHEDULER_ADD_NO_ENTITY_ID_KEY"
    elif add_entity_scalar_count > 1:
        reason = "SCHEDULER_ADD_TARGET_STRUCTURALLY_AMBIGUOUS"
    else:
        reason = "SCHEDULER_ADD_TARGET_NOT_IDENTIFIED"

    return {
        "top_level_mapping": top_level_mapping,
        "script_mapping_present": script_mapping_present,
        "expected_save_script_present": save_present,
        "expected_save_script_mapping": save_mapping,
        "expected_sequence_is_list": sequence_is_list,
        "scheduler_add_mapping_count": add_count,
        "scheduler_add_data_mapping_count": data_mapping_count,
        "scheduler_add_data_entity_scalar_count": data_entity_scalar_count,
        "scheduler_add_timeslots_key_count": timeslots_key_count,
        "scheduler_add_timeslots_list_count": timeslots_list_count,
        "scheduler_add_timeslots_string_count": timeslots_string_count,
        "scheduler_add_timeslots_entity_scalar_count": timeslots_entity_scalar_count,
        "scheduler_add_actions_key_count": actions_key_count,
        "scheduler_add_actions_list_count": actions_list_count,
        "scheduler_add_action_entry_mapping_count": action_entry_mapping_count,
        "scheduler_add_actions_entity_scalar_count": actions_entity_scalar_count,
        "scheduler_add_entity_id_key_count": entity_id_key_count,
        "scheduler_add_entity_id_scalar_count": entity_id_scalar_count,
        "scheduler_add_entity_scalar_count": add_entity_scalar_count,
        "shape_reason": reason,
    }

report = {
    "installed_home_assistant_yaml_loader_used": False,
    "home_assistant_secret_resolution_used": False,
    "runtime_error": False,
    "shape": {},
}

try:
    secrets = Secrets(ROOT)
    config = load_yaml_dict(str(SCHEDULER), secrets)
    report.update({
        "installed_home_assistant_yaml_loader_used": True,
        "home_assistant_secret_resolution_used": True,
        "shape": classify(config),
    })
except Exception:
    report["runtime_error"] = True

print(json.dumps(report, sort_keys=True))
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


def collect_subtree_probe(docker: str, container: str) -> dict[str, Any]:
    result = _run(
        [docker, "exec", "-i", container, "python", "-"],
        input_text=SUBTREE_PROBE,
    )
    if result.returncode != 0:
        raise RuntimeError("Scheduler-add subtree probe failed")
    try:
        decoded = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Scheduler-add subtree probe returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Scheduler-add subtree probe returned unexpected data")
    return decoded


def _privacy_flags() -> dict[str, bool]:
    return {
        "entity_ids_or_targets_emitted": False,
        "secret_aliases_emitted": False,
        "secret_values_emitted": False,
        "binding_hashes_emitted": False,
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "schedule_values_or_weekdays_emitted": False,
        "names_or_titles_emitted": False,
    }


def _mutation_flags() -> dict[str, bool]:
    return {
        "scheduler_service_called": False,
        "helper_state_changed": False,
        "scheduler_storage_written": False,
        "home_assistant_config_written": False,
        "dashboard_written": False,
        "heater_actuated": False,
        "reload_or_restart": False,
    }


def blocked_report(
    reason: str, *, expected: str | None = None, running: str | None = None
) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": BLOCKED,
        "reasons": [reason],
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": bool(expected is not None and running == expected),
        },
        "privacy": _privacy_flags(),
        "mutation": _mutation_flags(),
    }


def build_report(probe: dict[str, Any], *, expected: str, running: str) -> dict[str, Any]:
    if running != expected:
        return blocked_report(
            "HOME_ASSISTANT_VERSION_MISMATCH", expected=expected, running=running
        )
    if probe.get("runtime_error") is True:
        return blocked_report(
            "SCHEDULER_ADD_SUBTREE_PROBE_RUNTIME_ERROR",
            expected=expected,
            running=running,
        )

    shape = probe.get("shape")
    if not isinstance(shape, dict):
        return blocked_report(
            "SCHEDULER_ADD_SUBTREE_PROBE_INVALID", expected=expected, running=running
        )

    return {
        "schema": 1,
        "decision": DIAGNOSTIC_COMPLETE,
        "reasons": [],
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": True,
        },
        "installed_home_assistant_yaml_loader_used": probe.get(
            "installed_home_assistant_yaml_loader_used"
        )
        is True,
        "home_assistant_secret_resolution_used": probe.get(
            "home_assistant_secret_resolution_used"
        )
        is True,
        "shape": shape,
        "privacy": _privacy_flags(),
        "mutation": _mutation_flags(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose live Scheduler-add subtree without private values."
    )
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument(
        "--expected-version-file", type=Path, default=EXPECTED_VERSION_FILE
    )
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        expected = expected_version(args.expected_version_file)
        running = running_version(args.docker, args.container)
        probe = collect_subtree_probe(args.docker, args.container)
        report = build_report(probe, expected=expected, running=running)
    except (OSError, RuntimeError, ValueError):
        report = blocked_report("SCHEDULER_ADD_SUBTREE_DIAGNOSTIC_RUNTIME_ERROR")

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.stdout or True:
        sys.stdout.write(text)
    return 0 if report["decision"] == DIAGNOSTIC_COMPLETE else 20


if __name__ == "__main__":
    raise SystemExit(main())
