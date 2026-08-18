#!/usr/bin/env python3
"""Diagnose Scheduler target YAML shape without emitting private values."""

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
DIAGNOSTIC_COMPLETE = "SCHEDULER_TARGET_SHAPE_DIAGNOSTIC_COMPLETE"
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


def _count_scheduler_add_steps(value: Any) -> int:
    count = 0
    if isinstance(value, dict):
        if value.get("service") == "scheduler.add" or value.get("action") == "scheduler.add":
            count += 1
        count += sum(_count_scheduler_add_steps(item) for item in value.values())
    elif isinstance(value, list):
        count += sum(_count_scheduler_add_steps(item) for item in value)
    return count


def classify_scheduler_shape(config: Any) -> dict[str, Any]:
    top_level_mapping = isinstance(config, dict)
    scripts = config.get("script") if top_level_mapping else None
    script_mapping_present = isinstance(scripts, dict)
    script_entry_count = len(scripts) if script_mapping_present else -1

    save_present = bool(script_mapping_present and "heater_sched_save" in scripts)
    save_script = scripts.get("heater_sched_save") if script_mapping_present else None
    save_mapping = isinstance(save_script, dict)
    sequence = save_script.get("sequence") if save_mapping else None
    sequence_is_list = isinstance(sequence, list)
    sequence_step_count = len(sequence) if sequence_is_list else -1

    variable_step_count = 0
    variable_mapping_count = 0
    binding_key_occurrence_count = 0
    binding_value_is_string_count = 0
    binding_value_entity_scalar_count = 0

    if sequence_is_list:
        for step in sequence:
            if not isinstance(step, dict) or "variables" not in step:
                continue
            variable_step_count += 1
            variables = step.get("variables")
            if not isinstance(variables, dict):
                continue
            variable_mapping_count += 1
            if "heater_entity" not in variables:
                continue
            binding_key_occurrence_count += 1
            binding_value = variables.get("heater_entity")
            if isinstance(binding_value, str):
                binding_value_is_string_count += 1
            if _entity_scalar(binding_value):
                binding_value_entity_scalar_count += 1

    scheduler_add_step_count = _count_scheduler_add_steps(save_script) if save_mapping else 0
    save_script_entity_scalar_count = _count_entity_scalars(save_script) if save_mapping else 0

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
    elif binding_key_occurrence_count == 0:
        reason = "EXPECTED_BINDING_VARIABLE_MISSING"
    elif binding_key_occurrence_count > 1:
        reason = "EXPECTED_BINDING_VARIABLE_AMBIGUOUS"
    elif binding_value_is_string_count != 1 or binding_value_entity_scalar_count != 1:
        reason = "EXPECTED_BINDING_VALUE_SHAPE_INVALID"
    elif scheduler_add_step_count != 1:
        reason = "SCHEDULER_ADD_STEP_NOT_EXACT"
    else:
        reason = "EXPECTED_SCHEDULER_TARGET_SHAPE_EXACT"

    return {
        "top_level_mapping": top_level_mapping,
        "script_mapping_present": script_mapping_present,
        "script_entry_count": script_entry_count,
        "expected_save_script_present": save_present,
        "expected_save_script_mapping": save_mapping,
        "expected_sequence_is_list": sequence_is_list,
        "expected_sequence_step_count": sequence_step_count,
        "variable_step_count": variable_step_count,
        "variable_mapping_count": variable_mapping_count,
        "expected_binding_key_occurrence_count": binding_key_occurrence_count,
        "expected_binding_value_is_string_count": binding_value_is_string_count,
        "expected_binding_value_entity_scalar_count": binding_value_entity_scalar_count,
        "scheduler_add_step_count": scheduler_add_step_count,
        "save_script_entity_scalar_count": save_script_entity_scalar_count,
        "shape_reason": reason,
    }


SHAPE_PROBE = r'''
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


def count_scheduler_add_steps(value):
    count = 0
    if isinstance(value, dict):
        if value.get("service") == "scheduler.add" or value.get("action") == "scheduler.add":
            count += 1
        count += sum(count_scheduler_add_steps(item) for item in value.values())
    elif isinstance(value, list):
        count += sum(count_scheduler_add_steps(item) for item in value)
    return count


def classify(config):
    top_level_mapping = isinstance(config, dict)
    scripts = config.get("script") if top_level_mapping else None
    script_mapping_present = isinstance(scripts, dict)
    script_entry_count = len(scripts) if script_mapping_present else -1

    save_present = bool(script_mapping_present and "heater_sched_save" in scripts)
    save_script = scripts.get("heater_sched_save") if script_mapping_present else None
    save_mapping = isinstance(save_script, dict)
    sequence = save_script.get("sequence") if save_mapping else None
    sequence_is_list = isinstance(sequence, list)
    sequence_step_count = len(sequence) if sequence_is_list else -1

    variable_step_count = 0
    variable_mapping_count = 0
    binding_key_occurrence_count = 0
    binding_value_is_string_count = 0
    binding_value_entity_scalar_count = 0

    if sequence_is_list:
        for step in sequence:
            if not isinstance(step, dict) or "variables" not in step:
                continue
            variable_step_count += 1
            variables = step.get("variables")
            if not isinstance(variables, dict):
                continue
            variable_mapping_count += 1
            if "heater_entity" not in variables:
                continue
            binding_key_occurrence_count += 1
            binding_value = variables.get("heater_entity")
            if isinstance(binding_value, str):
                binding_value_is_string_count += 1
            if entity_scalar(binding_value):
                binding_value_entity_scalar_count += 1

    scheduler_add_step_count = count_scheduler_add_steps(save_script) if save_mapping else 0
    save_script_entity_scalar_count = count_entity_scalars(save_script) if save_mapping else 0

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
    elif binding_key_occurrence_count == 0:
        reason = "EXPECTED_BINDING_VARIABLE_MISSING"
    elif binding_key_occurrence_count > 1:
        reason = "EXPECTED_BINDING_VARIABLE_AMBIGUOUS"
    elif binding_value_is_string_count != 1 or binding_value_entity_scalar_count != 1:
        reason = "EXPECTED_BINDING_VALUE_SHAPE_INVALID"
    elif scheduler_add_step_count != 1:
        reason = "SCHEDULER_ADD_STEP_NOT_EXACT"
    else:
        reason = "EXPECTED_SCHEDULER_TARGET_SHAPE_EXACT"

    return {
        "top_level_mapping": top_level_mapping,
        "script_mapping_present": script_mapping_present,
        "script_entry_count": script_entry_count,
        "expected_save_script_present": save_present,
        "expected_save_script_mapping": save_mapping,
        "expected_sequence_is_list": sequence_is_list,
        "expected_sequence_step_count": sequence_step_count,
        "variable_step_count": variable_step_count,
        "variable_mapping_count": variable_mapping_count,
        "expected_binding_key_occurrence_count": binding_key_occurrence_count,
        "expected_binding_value_is_string_count": binding_value_is_string_count,
        "expected_binding_value_entity_scalar_count": binding_value_entity_scalar_count,
        "scheduler_add_step_count": scheduler_add_step_count,
        "save_script_entity_scalar_count": save_script_entity_scalar_count,
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


def collect_shape_probe(docker: str, container: str) -> dict[str, Any]:
    result = _run(
        [docker, "exec", "-i", container, "python", "-"],
        input_text=SHAPE_PROBE,
    )
    if result.returncode != 0:
        raise RuntimeError("Scheduler target shape probe failed")
    try:
        decoded = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Scheduler target shape probe returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Scheduler target shape probe returned unexpected data")
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


def blocked_report(reason: str, *, expected: str | None = None, running: str | None = None) -> dict[str, Any]:
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
        return blocked_report("HOME_ASSISTANT_VERSION_MISMATCH", expected=expected, running=running)
    if probe.get("runtime_error") is True:
        return blocked_report("SCHEDULER_TARGET_SHAPE_PROBE_RUNTIME_ERROR", expected=expected, running=running)

    shape = probe.get("shape")
    if not isinstance(shape, dict):
        return blocked_report("SCHEDULER_TARGET_SHAPE_PROBE_INVALID", expected=expected, running=running)

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
        ) is True,
        "home_assistant_secret_resolution_used": probe.get(
            "home_assistant_secret_resolution_used"
        ) is True,
        "shape": shape,
        "privacy": _privacy_flags(),
        "mutation": _mutation_flags(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose live Scheduler target YAML shape without private values."
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
        probe = collect_shape_probe(args.docker, args.container)
        report = build_report(probe, expected=expected, running=running)
    except (OSError, RuntimeError, ValueError):
        report = blocked_report("SCHEDULER_TARGET_SHAPE_DIAGNOSTIC_RUNTIME_ERROR")

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.stdout or True:
        sys.stdout.write(text)
    return 0 if report["decision"] == DIAGNOSTIC_COMPLETE else 20


if __name__ == "__main__":
    raise SystemExit(main())
