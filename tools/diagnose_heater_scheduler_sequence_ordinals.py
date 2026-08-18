#!/usr/bin/env python3
"""Classify Scheduler save-script sequence ordinals without private values."""

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
DIAGNOSTIC_COMPLETE = "SCHEDULER_SEQUENCE_ORDINAL_DIAGNOSTIC_COMPLETE"
BLOCKED = "BLOCKED"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def _entity_scalar(value: Any) -> bool:
    return isinstance(value, str) and ENTITY_ID.fullmatch(value) is not None


def _count_entity_refs(value: Any) -> int:
    if _entity_scalar(value):
        return 1
    if isinstance(value, dict):
        return sum(
            _count_entity_refs(item)
            for key, item in value.items()
            if key not in {"service", "action"}
        )
    if isinstance(value, list):
        return sum(_count_entity_refs(item) for item in value)
    return 0


def _count_key(value: Any, wanted: str) -> tuple[int, int, int]:
    key_count = 0
    mapping_count = 0
    entity_scalar_count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == wanted:
                key_count += 1
                if isinstance(item, dict):
                    mapping_count += 1
                if _entity_scalar(item):
                    entity_scalar_count += 1
            nested = _count_key(item, wanted)
            key_count += nested[0]
            mapping_count += nested[1]
            entity_scalar_count += nested[2]
    elif isinstance(value, list):
        for item in value:
            nested = _count_key(item, wanted)
            key_count += nested[0]
            mapping_count += nested[1]
            entity_scalar_count += nested[2]
    return key_count, mapping_count, entity_scalar_count


def _is_scheduler_add_step(step: Any) -> bool:
    return bool(
        isinstance(step, dict)
        and (step.get("service") == "scheduler.add" or step.get("action") == "scheduler.add")
    )


def classify_sequence_ordinals(config: Any) -> dict[str, Any]:
    top_mapping = isinstance(config, dict)
    scripts = config.get("script") if top_mapping else None
    scripts_mapping = isinstance(scripts, dict)
    save = scripts.get("heater_sched_save") if scripts_mapping else None
    save_mapping = isinstance(save, dict)
    sequence = save.get("sequence") if save_mapping else None
    sequence_list = isinstance(sequence, list)

    rows: list[dict[str, Any]] = []
    if sequence_list:
        for ordinal, step in enumerate(sequence):
            variables = step.get("variables") if isinstance(step, dict) else None
            variables_mapping = isinstance(variables, dict)
            target = _count_key(step, "target")
            entity_id = _count_key(step, "entity_id")
            entity_refs = _count_entity_refs(step)
            rows.append(
                {
                    "ordinal": ordinal,
                    "step_mapping": isinstance(step, dict),
                    "variables_mapping": variables_mapping,
                    "variables_entity_reference_scalar_count": (
                        _count_entity_refs(variables) if variables_mapping else 0
                    ),
                    "target_key_count": target[0],
                    "target_mapping_count": target[1],
                    "target_entity_reference_scalar_count": (
                        _count_entity_refs(step.get("target"))
                        if isinstance(step, dict) and "target" in step
                        else 0
                    ),
                    "entity_id_key_count": entity_id[0],
                    "entity_id_scalar_count": entity_id[2],
                    "entity_reference_scalar_count": entity_refs,
                    "scheduler_add_step": _is_scheduler_add_step(step),
                }
            )

    total_refs = sum(row["entity_reference_scalar_count"] for row in rows)
    populated = sum(row["entity_reference_scalar_count"] > 0 for row in rows)
    scheduler_add_ordinals = sum(row["scheduler_add_step"] for row in rows)

    if not top_mapping:
        reason = "TOP_LEVEL_NOT_MAPPING"
    elif not scripts_mapping:
        reason = "SCRIPT_MAPPING_MISSING"
    elif not save_mapping:
        reason = "EXPECTED_SAVE_SCRIPT_MISSING_OR_INVALID"
    elif not sequence_list:
        reason = "EXPECTED_SEQUENCE_NOT_LIST"
    elif scheduler_add_ordinals != 1:
        reason = "SCHEDULER_ADD_ORDINAL_NOT_EXACT"
    elif populated == 0:
        reason = "NO_ENTITY_REFERENCE_ORDINALS"
    elif populated == 1:
        reason = "UNIQUE_ENTITY_REFERENCE_ORDINAL"
    else:
        reason = "MULTIPLE_ENTITY_REFERENCE_ORDINALS"

    return {
        "top_level_mapping": top_mapping,
        "script_mapping_present": scripts_mapping,
        "expected_save_script_mapping": save_mapping,
        "expected_sequence_is_list": sequence_list,
        "sequence_step_count": len(sequence) if sequence_list else -1,
        "scheduler_add_ordinal_count": scheduler_add_ordinals,
        "entity_reference_scalar_total": total_refs,
        "entity_reference_populated_ordinal_count": populated,
        "ordinals": rows,
        "shape_reason": reason,
    }


PROBE = r'''
import json
import re
from pathlib import Path
from homeassistant.util.yaml import Secrets, load_yaml_dict

ROOT = Path("/config")
SCHEDULER = ROOT / "packages" / "heater_scheduler.yaml"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

def entity_scalar(value):
    return isinstance(value, str) and ENTITY_ID.fullmatch(value) is not None

def count_entity_refs(value):
    if entity_scalar(value):
        return 1
    if isinstance(value, dict):
        return sum(count_entity_refs(item) for key, item in value.items() if key not in {"service", "action"})
    if isinstance(value, list):
        return sum(count_entity_refs(item) for item in value)
    return 0

def count_key(value, wanted):
    key_count = mapping_count = entity_scalar_count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key == wanted:
                key_count += 1
                mapping_count += int(isinstance(item, dict))
                entity_scalar_count += int(entity_scalar(item))
            nested = count_key(item, wanted)
            key_count += nested[0]
            mapping_count += nested[1]
            entity_scalar_count += nested[2]
    elif isinstance(value, list):
        for item in value:
            nested = count_key(item, wanted)
            key_count += nested[0]
            mapping_count += nested[1]
            entity_scalar_count += nested[2]
    return key_count, mapping_count, entity_scalar_count

def scheduler_add_step(step):
    return bool(isinstance(step, dict) and (step.get("service") == "scheduler.add" or step.get("action") == "scheduler.add"))

def classify(config):
    top_mapping = isinstance(config, dict)
    scripts = config.get("script") if top_mapping else None
    scripts_mapping = isinstance(scripts, dict)
    save = scripts.get("heater_sched_save") if scripts_mapping else None
    save_mapping = isinstance(save, dict)
    sequence = save.get("sequence") if save_mapping else None
    sequence_list = isinstance(sequence, list)
    rows = []
    if sequence_list:
        for ordinal, step in enumerate(sequence):
            variables = step.get("variables") if isinstance(step, dict) else None
            variables_mapping = isinstance(variables, dict)
            target = count_key(step, "target")
            entity_id = count_key(step, "entity_id")
            rows.append({
                "ordinal": ordinal,
                "step_mapping": isinstance(step, dict),
                "variables_mapping": variables_mapping,
                "variables_entity_reference_scalar_count": count_entity_refs(variables) if variables_mapping else 0,
                "target_key_count": target[0],
                "target_mapping_count": target[1],
                "target_entity_reference_scalar_count": count_entity_refs(step.get("target")) if isinstance(step, dict) and "target" in step else 0,
                "entity_id_key_count": entity_id[0],
                "entity_id_scalar_count": entity_id[2],
                "entity_reference_scalar_count": count_entity_refs(step),
                "scheduler_add_step": scheduler_add_step(step),
            })
    total_refs = sum(row["entity_reference_scalar_count"] for row in rows)
    populated = sum(row["entity_reference_scalar_count"] > 0 for row in rows)
    scheduler_add_ordinals = sum(row["scheduler_add_step"] for row in rows)
    if not top_mapping:
        reason = "TOP_LEVEL_NOT_MAPPING"
    elif not scripts_mapping:
        reason = "SCRIPT_MAPPING_MISSING"
    elif not save_mapping:
        reason = "EXPECTED_SAVE_SCRIPT_MISSING_OR_INVALID"
    elif not sequence_list:
        reason = "EXPECTED_SEQUENCE_NOT_LIST"
    elif scheduler_add_ordinals != 1:
        reason = "SCHEDULER_ADD_ORDINAL_NOT_EXACT"
    elif populated == 0:
        reason = "NO_ENTITY_REFERENCE_ORDINALS"
    elif populated == 1:
        reason = "UNIQUE_ENTITY_REFERENCE_ORDINAL"
    else:
        reason = "MULTIPLE_ENTITY_REFERENCE_ORDINALS"
    return {
        "top_level_mapping": top_mapping,
        "script_mapping_present": scripts_mapping,
        "expected_save_script_mapping": save_mapping,
        "expected_sequence_is_list": sequence_list,
        "sequence_step_count": len(sequence) if sequence_list else -1,
        "scheduler_add_ordinal_count": scheduler_add_ordinals,
        "entity_reference_scalar_total": total_refs,
        "entity_reference_populated_ordinal_count": populated,
        "ordinals": rows,
        "shape_reason": reason,
    }

report = {"runtime_error": False, "installed_home_assistant_yaml_loader_used": False, "home_assistant_secret_resolution_used": False, "shape": {}}
try:
    secrets = Secrets(ROOT)
    config = load_yaml_dict(str(SCHEDULER), secrets)
    report.update({"installed_home_assistant_yaml_loader_used": True, "home_assistant_secret_resolution_used": True, "shape": classify(config)})
except Exception:
    report["runtime_error"] = True
print(json.dumps(report, sort_keys=True))
'''


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def collect_probe(docker: str, container: str) -> dict[str, Any]:
    result = _run([docker, "exec", "-i", container, "python", "-"], input_text=PROBE)
    if result.returncode != 0:
        raise RuntimeError("sequence ordinal probe failed")
    decoded = json.loads(result.stdout.strip())
    if not isinstance(decoded, dict):
        raise RuntimeError("sequence ordinal probe returned unexpected data")
    return decoded


def _privacy() -> dict[str, bool]:
    return {
        "entity_ids_or_targets_emitted": False,
        "secret_aliases_emitted": False,
        "secret_values_emitted": False,
        "binding_hashes_emitted": False,
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "schedule_values_or_weekdays_emitted": False,
        "names_titles_or_templates_emitted": False,
    }


def _mutation() -> dict[str, bool]:
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
        "home_assistant": {"expected_version": expected, "running_version": running, "version_match": bool(expected is not None and running == expected)},
        "privacy": _privacy(),
        "mutation": _mutation(),
    }


def build_report(probe: dict[str, Any], *, expected: str, running: str) -> dict[str, Any]:
    if running != expected:
        return blocked_report("HOME_ASSISTANT_VERSION_MISMATCH", expected=expected, running=running)
    if probe.get("runtime_error") is True:
        return blocked_report("SEQUENCE_ORDINAL_PROBE_RUNTIME_ERROR", expected=expected, running=running)
    shape = probe.get("shape")
    if not isinstance(shape, dict):
        return blocked_report("SEQUENCE_ORDINAL_PROBE_INVALID", expected=expected, running=running)
    return {
        "schema": 1,
        "decision": DIAGNOSTIC_COMPLETE,
        "reasons": [],
        "home_assistant": {"expected_version": expected, "running_version": running, "version_match": True},
        "installed_home_assistant_yaml_loader_used": probe.get("installed_home_assistant_yaml_loader_used") is True,
        "home_assistant_secret_resolution_used": probe.get("home_assistant_secret_resolution_used") is True,
        "shape": shape,
        "privacy": _privacy(),
        "mutation": _mutation(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose live Scheduler sequence ordinals without private values.")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--expected-version-file", type=Path, default=EXPECTED_VERSION_FILE)
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        expected = expected_version(args.expected_version_file)
        running = running_version(args.docker, args.container)
        report = build_report(collect_probe(args.docker, args.container), expected=expected, running=running)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        report = blocked_report("SEQUENCE_ORDINAL_DIAGNOSTIC_RUNTIME_ERROR")
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report["decision"] == DIAGNOSTIC_COMPLETE else 20


if __name__ == "__main__":
    raise SystemExit(main())
