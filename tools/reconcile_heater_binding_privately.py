#!/usr/bin/env python3
"""Reconcile #9B heater binding equality without emitting private values."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.plan_heater_disabled_legacy_transition import (
    BLOCKED,
    READY,
    build_report,
    candidate_summary,
    collect_runtime_probe,
    collect_supplemental_probe,
    expected_version,
    running_version,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONTAINER = "homeassistant"

PRIVATE_BINDING_PROBE = r'''
import hmac
import json
import re
from pathlib import Path

from homeassistant.util.yaml import Secrets, load_yaml_dict

ROOT = Path("/config")
LEGACY = ROOT / "packages" / "silditajs.yaml"
SCHEDULER = ROOT / "packages" / "heater_scheduler.yaml"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def entity_scalar(value):
    if not isinstance(value, str):
        return None
    value = str(value)
    return value if ENTITY_ID.fullmatch(value) else None


def automation_by_id(config, automation_id):
    automations = config.get("automation") if isinstance(config, dict) else None
    if not isinstance(automations, list):
        return None
    matches = [
        item
        for item in automations
        if isinstance(item, dict) and item.get("id") == automation_id
    ]
    return matches[0] if len(matches) == 1 else None


def action_target(automation, service):
    if not isinstance(automation, dict):
        return None
    actions = automation.get("action")
    if not isinstance(actions, list):
        return None
    matches = []
    for action in actions:
        if not isinstance(action, dict) or action.get("service") != service:
            continue
        target = action.get("target")
        if not isinstance(target, dict):
            continue
        value = entity_scalar(target.get("entity_id"))
        if value is not None:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


def scheduler_target(config):
    scripts = config.get("script") if isinstance(config, dict) else None
    script = scripts.get("heater_sched_save") if isinstance(scripts, dict) else None
    sequence = script.get("sequence") if isinstance(script, dict) else None
    if not isinstance(sequence, list):
        return None
    matches = []
    for step in sequence:
        if not isinstance(step, dict):
            continue
        variables = step.get("variables")
        if not isinstance(variables, dict) or "heater_entity" not in variables:
            continue
        value = entity_scalar(variables.get("heater_entity"))
        if value is not None:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


report = {
    "installed_home_assistant_yaml_loader_used": False,
    "home_assistant_secret_resolution_used": False,
    "runtime_error": False,
    "legacy_on_target_resolved": False,
    "legacy_off_target_resolved": False,
    "scheduler_target_resolved": False,
    "timer_target_resolved": False,
    "legacy_on_off_equal": False,
    "legacy_scheduler_equal": False,
    "timer_scheduler_equal": False,
    "all_four_targets_equal": False,
    "privacy": {
        "entity_ids_or_targets_emitted": False,
        "secret_aliases_emitted": False,
        "secret_values_emitted": False,
        "binding_hashes_emitted": False,
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
    },
}

try:
    secrets = Secrets(ROOT)
    legacy = load_yaml_dict(str(LEGACY), secrets)
    scheduler = load_yaml_dict(str(SCHEDULER), secrets)

    legacy_on = action_target(
        automation_by_id(legacy, "silditajs_grafiks_on"),
        "switch.turn_on",
    )
    legacy_off = action_target(
        automation_by_id(legacy, "silditajs_grafiks_off"),
        "switch.turn_off",
    )
    timer = action_target(
        automation_by_id(legacy, "silditajs_auto_off"),
        "switch.turn_off",
    )
    scheduler_value = scheduler_target(scheduler)

    values = (legacy_on, legacy_off, scheduler_value, timer)
    resolved = tuple(isinstance(value, str) for value in values)
    all_resolved = all(resolved)

    legacy_on_off_equal = bool(
        all_resolved and hmac.compare_digest(legacy_on, legacy_off)
    )
    legacy_scheduler_equal = bool(
        all_resolved and hmac.compare_digest(legacy_on, scheduler_value)
    )
    timer_scheduler_equal = bool(
        all_resolved and hmac.compare_digest(timer, scheduler_value)
    )

    report.update(
        {
            "installed_home_assistant_yaml_loader_used": True,
            "home_assistant_secret_resolution_used": True,
            "legacy_on_target_resolved": resolved[0],
            "legacy_off_target_resolved": resolved[1],
            "scheduler_target_resolved": resolved[2],
            "timer_target_resolved": resolved[3],
            "legacy_on_off_equal": legacy_on_off_equal,
            "legacy_scheduler_equal": legacy_scheduler_equal,
            "timer_scheduler_equal": timer_scheduler_equal,
            "all_four_targets_equal": bool(
                legacy_on_off_equal
                and legacy_scheduler_equal
                and timer_scheduler_equal
            ),
        }
    )
except Exception:
    report["runtime_error"] = True

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


def collect_private_binding_probe(
    docker: str, container: str
) -> dict[str, Any]:
    result = _run(
        [docker, "exec", "-i", container, "python", "-"],
        input_text=PRIVATE_BINDING_PROBE,
    )
    if result.returncode != 0:
        raise RuntimeError("private heater binding probe failed")
    try:
        decoded = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("private heater binding probe returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("private heater binding probe returned unexpected data")
    return decoded


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
        "privacy": {
            "entity_ids_or_targets_emitted": False,
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "binding_hashes_emitted": False,
            "raw_yaml_emitted": False,
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


def _original_is_binding_only_blocked(original: dict[str, Any]) -> bool:
    source = original.get("source_reconciliation")
    if not isinstance(source, dict):
        return False
    expected_true = (
        "candidate_binding_token_present",
        "legacy_daily_off_semantics_exact",
        "legacy_daily_on_semantics_exact",
        "legacy_direct_automations_removed",
        "legacy_schedule_helper_removed",
        "legacy_time_helpers_removed",
        "scheduler_save_script_present",
        "timer_preserved",
    )
    return bool(
        original.get("decision") == BLOCKED
        and original.get("reasons") == ["LEGACY_SOURCE_SEMANTICS_NOT_EXACT"]
        and all(source.get(key) is True for key in expected_true)
        and source.get("shared_binding_token_proven_without_secret_read") is False
    )


def _binding_proof_passes(binding: dict[str, Any]) -> bool:
    required_true = (
        "installed_home_assistant_yaml_loader_used",
        "home_assistant_secret_resolution_used",
        "legacy_on_target_resolved",
        "legacy_off_target_resolved",
        "scheduler_target_resolved",
        "timer_target_resolved",
        "legacy_on_off_equal",
        "legacy_scheduler_equal",
        "timer_scheduler_equal",
        "all_four_targets_equal",
    )
    privacy = binding.get("privacy")
    return bool(
        binding.get("runtime_error") is False
        and all(binding.get(key) is True for key in required_true)
        and isinstance(privacy, dict)
        and all(value is False for value in privacy.values())
    )


def reconcile_report(
    original: dict[str, Any], binding: dict[str, Any]
) -> dict[str, Any]:
    if not _original_is_binding_only_blocked(original):
        return blocked_report("ORIGINAL_PLANNER_NOT_BINDING_ONLY_BLOCKED")

    if binding.get("runtime_error") is True:
        return blocked_report("HOME_ASSISTANT_YAML_RESOLUTION_FAILED")

    resolved_keys = (
        "legacy_on_target_resolved",
        "legacy_off_target_resolved",
        "scheduler_target_resolved",
        "timer_target_resolved",
    )
    if not all(binding.get(key) is True for key in resolved_keys):
        return blocked_report("PRIVATE_BINDING_TARGET_SHAPE_INVALID")

    if not _binding_proof_passes(binding):
        return blocked_report("PRIVATE_BINDING_VALUES_NOT_EQUAL")

    report = copy.deepcopy(original)
    report["decision"] = READY
    report["reasons"] = []
    source = report["source_reconciliation"]
    source["shared_binding_value_proven_privately"] = True

    report["binding_reconciliation"] = {
        "original_literal_token_proof_succeeded": False,
        "resolved_value_equality_proof_succeeded": True,
        "legacy_on_off_equal": True,
        "legacy_scheduler_equal": True,
        "timer_scheduler_equal": True,
        "all_four_targets_equal": True,
    }
    report["private_resolution"] = {
        "installed_home_assistant_yaml_loader_used": True,
        "home_assistant_secret_resolution_used": True,
        "private_binding_values_read_for_equality_only": True,
        "private_binding_values_emitted": False,
        "private_binding_hashes_emitted": False,
        "private_binding_values_persisted": False,
    }
    report["transition"] = {
        "no_bootstrap_preserves_current_active_off_behavior": True,
        "no_bootstrap_retires_latent_legacy_time_values": True,
        "scheduler_bootstrap_requires_separate_state_preserving_authorization": True,
        "scheduler_add_entries_default_enabled": True,
        "scheduler_add_service_has_enabled_argument": False,
    }
    report["claims"] = {
        "owner_choice_made": False,
        "scheduler_bootstrap_authorized": False,
        "production_apply_authorized": False,
    }
    report["privacy"] = {
        "entity_ids_or_targets_emitted": False,
        "legacy_time_values_emitted": False,
        "secret_aliases_emitted": False,
        "secret_values_emitted": False,
        "binding_hashes_emitted": False,
        "raw_storage_emitted": False,
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
    }
    report["mutation"] = {
        "scheduler_service_called": False,
        "helper_state_changed": False,
        "scheduler_storage_written": False,
        "home_assistant_config_written": False,
        "dashboard_written": False,
        "heater_actuated": False,
        "reload_or_restart": False,
    }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile the #9B private heater binding safely."
    )
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument(
        "--expected-version-file", type=Path, default=EXPECTED_VERSION_FILE
    )
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.reconcile:
        report = blocked_report("RECONCILIATION_GATE_REQUIRED")
    else:
        try:
            expected = expected_version(args.expected_version_file)
            running = running_version(args.docker, args.container)
            base = collect_runtime_probe(args.docker, args.container)
            supplemental = collect_supplemental_probe(args.docker, args.container)
            original = build_report(
                base,
                supplemental,
                candidate_summary(),
                expected=expected,
                running=running,
            )
            binding = collect_private_binding_probe(args.docker, args.container)
            report = reconcile_report(original, binding)
        except (OSError, RuntimeError, ValueError):
            report = blocked_report("PRIVATE_BINDING_RECONCILIATION_RUNTIME_ERROR")

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.stdout or True:
        sys.stdout.write(text)
    return 0 if report["decision"] == READY else 20


if __name__ == "__main__":
    raise SystemExit(main())
