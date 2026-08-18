#!/usr/bin/env python3
"""Reconcile heater binding equality with a fail-closed Scheduler template fallback."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.plan_heater_disabled_legacy_transition import (
    build_report,
    candidate_summary,
    collect_runtime_probe,
    collect_supplemental_probe,
    expected_version,
    running_version,
)
from tools.reconcile_heater_binding_privately import blocked_report, reconcile_report

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONTAINER = "homeassistant"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
QUOTED_ENTITY_ID_FIELD = re.compile(
    r"(?:'entity_id'|\"entity_id\")\s*:\s*(['\"])([a-z0-9_]+\.[a-z0-9_]+)\1"
)


def _entity_scalar(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if ENTITY_ID.fullmatch(value) else None


def scheduler_target_from_config(config: Any) -> str | None:
    """Return one private Scheduler target or None without broad inference."""
    scripts = config.get("script") if isinstance(config, dict) else None
    script = scripts.get("heater_sched_save") if isinstance(scripts, dict) else None
    sequence = script.get("sequence") if isinstance(script, dict) else None
    if not isinstance(sequence, list):
        return None

    variable_matches: list[str] = []
    for step in sequence:
        if not isinstance(step, dict):
            continue
        variables = step.get("variables")
        if not isinstance(variables, dict) or "heater_entity" not in variables:
            continue
        value = _entity_scalar(variables.get("heater_entity"))
        if value is not None:
            variable_matches.append(value)

    if len(variable_matches) == 1:
        return variable_matches[0]
    if len(variable_matches) > 1:
        return None

    scheduler_steps = [
        step
        for step in sequence
        if isinstance(step, dict)
        and (
            step.get("service") == "scheduler.add"
            or step.get("action") == "scheduler.add"
        )
    ]
    if len(scheduler_steps) != 1:
        return None

    data = scheduler_steps[0].get("data")
    timeslots = data.get("timeslots") if isinstance(data, dict) else None
    if not isinstance(timeslots, str):
        return None

    matches = [match.group(2) for match in QUOTED_ENTITY_ID_FIELD.finditer(timeslots)]
    matches = [value for value in matches if _entity_scalar(value) is not None]
    return matches[0] if len(matches) == 1 else None


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
QUOTED_ENTITY_ID_FIELD = re.compile(
    r"(?:'entity_id'|\"entity_id\")\s*:\s*(['\"])([a-z0-9_]+\.[a-z0-9_]+)\1"
)


def entity_scalar(value):
    if not isinstance(value, str):
        return None
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

    variable_matches = []
    for step in sequence:
        if not isinstance(step, dict):
            continue
        variables = step.get("variables")
        if not isinstance(variables, dict) or "heater_entity" not in variables:
            continue
        value = entity_scalar(variables.get("heater_entity"))
        if value is not None:
            variable_matches.append(value)

    if len(variable_matches) == 1:
        return variable_matches[0]
    if len(variable_matches) > 1:
        return None

    scheduler_steps = [
        step
        for step in sequence
        if isinstance(step, dict)
        and (
            step.get("service") == "scheduler.add"
            or step.get("action") == "scheduler.add"
        )
    ]
    if len(scheduler_steps) != 1:
        return None

    data = scheduler_steps[0].get("data")
    timeslots = data.get("timeslots") if isinstance(data, dict) else None
    if not isinstance(timeslots, str):
        return None

    matches = [match.group(2) for match in QUOTED_ENTITY_ID_FIELD.finditer(timeslots)]
    matches = [value for value in matches if entity_scalar(value) is not None]
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


def collect_private_binding_probe(docker: str, container: str) -> dict[str, Any]:
    result = _run(
        [docker, "exec", "-i", container, "python", "-"],
        input_text=PRIVATE_BINDING_PROBE,
    )
    if result.returncode != 0:
        raise RuntimeError("private template binding probe failed")
    try:
        decoded = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("private template binding probe returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("private template binding probe returned unexpected data")
    return decoded


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile #9 heater binding with a private Scheduler template fallback."
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
            original = build_report(
                collect_runtime_probe(args.docker, args.container),
                collect_supplemental_probe(args.docker, args.container),
                candidate_summary(),
                expected=expected,
                running=running,
            )
            binding = collect_private_binding_probe(args.docker, args.container)
            report = reconcile_report(original, binding)
        except (OSError, RuntimeError, ValueError):
            report = blocked_report("PRIVATE_BINDING_RECONCILIATION_RUNTIME_ERROR")

    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report.get("decision") != "BLOCKED" else 20


if __name__ == "__main__":
    raise SystemExit(main())
