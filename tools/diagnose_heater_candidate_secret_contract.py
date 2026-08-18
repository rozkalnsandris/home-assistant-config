#!/usr/bin/env python3
"""Diagnose heater candidate secret-contract cardinality without emitting aliases."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONTAINER = "homeassistant"
CANDIDATES = (
    ROOT / "packages" / "silditajs.yaml",
    ROOT / "packages" / "heater_scheduler.yaml",
)
SECRET_REF = re.compile(r"!secret\s+([A-Za-z0-9_]+)")
SECRET_TOKEN = re.compile(r"^[A-Za-z0-9_]+$")


PRIVATE_PROBE = r'''
import json
import sys
from pathlib import Path

from homeassistant.util.yaml import Secrets, parse_yaml

contracts = json.loads(sys.stdin.read())
secrets = Secrets(Path("/config"))
items = []

for ordinal, aliases in enumerate(contracts, start=1):
    resolvable = 0
    missing = 0
    runtime_error = False

    for alias in aliases:
        try:
            parsed = parse_yaml(f"probe: !secret {alias}\n", secrets)
        except Exception:
            missing += 1
            continue

        if not isinstance(parsed, dict) or "probe" not in parsed:
            runtime_error = True
            continue

        resolvable += 1

    items.append(
        {
            "ordinal": ordinal,
            "required_count": len(aliases),
            "resolvable_count": resolvable,
            "missing_count": missing,
            "runtime_error": runtime_error,
        }
    )

print(json.dumps({"items": items}, sort_keys=True))
'''


def expected_version(path: Path = EXPECTED_VERSION_FILE) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("expected version unavailable")
    return value


def running_version(docker: str, container: str) -> str:
    result = subprocess.run(
        [docker, "exec", container, "python", "-m", "homeassistant", "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("running version unavailable")
    return result.stdout.strip()


def extract_secret_contract(text: str) -> list[str]:
    """Return unique secret tokens without preserving occurrence order."""
    return sorted(set(SECRET_REF.findall(text)))


def candidate_contracts(paths: tuple[Path, ...] = CANDIDATES) -> list[list[str]]:
    contracts: list[list[str]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise OSError("candidate unavailable")
        contracts.append(extract_secret_contract(path.read_text(encoding="utf-8")))
    return contracts


def validate_contracts(contracts: list[list[str]]) -> None:
    if len(contracts) != len(CANDIDATES):
        raise ValueError("candidate count mismatch")
    for aliases in contracts:
        if not aliases:
            raise ValueError("candidate has no secret contract")
        if len(aliases) != len(set(aliases)):
            raise ValueError("candidate secret contract is not unique")
        if any(SECRET_TOKEN.fullmatch(alias) is None for alias in aliases):
            raise ValueError("candidate secret token shape invalid")


def private_probe(
    docker: str, container: str, contracts: list[list[str]]
) -> list[dict[str, Any]]:
    result = subprocess.run(
        [docker, "exec", "-i", container, "python", "-c", PRIVATE_PROBE],
        input=json.dumps(contracts),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("private secret-contract probe failed")
    try:
        decoded = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("private secret-contract probe returned invalid JSON") from exc
    items = decoded.get("items") if isinstance(decoded, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("private secret-contract probe returned unexpected data")
    return items


def sanitize_items(items: list[dict[str, Any]]) -> list[dict[str, int]]:
    sanitized: list[dict[str, int]] = []
    for expected_ordinal, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError("probe item invalid")
        if item.get("ordinal") != expected_ordinal:
            raise ValueError("probe ordinal invalid")
        if item.get("runtime_error") is not False:
            raise ValueError("probe runtime error")

        required = item.get("required_count")
        resolvable = item.get("resolvable_count")
        missing = item.get("missing_count")
        if not all(isinstance(value, int) and value >= 0 for value in (required, resolvable, missing)):
            raise ValueError("probe count invalid")
        if resolvable + missing != required:
            raise ValueError("probe count reconciliation failed")

        sanitized.append(
            {
                "ordinal": expected_ordinal,
                "required_count": required,
                "resolvable_count": resolvable,
                "missing_count": missing,
            }
        )
    return sanitized


def blocked_report(reason: str, expected: str | None = None) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": [reason],
        "home_assistant": {
            "expected_version": expected,
            "running_version": None,
            "version_match": False,
        },
        "contracts": [],
        "privacy": {
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "raw_yaml_emitted": False,
            "raw_exception_text_emitted": False,
            "private_paths_emitted": False,
        },
        "mutation": {
            "home_assistant_config_written": False,
            "scheduler_service_called": False,
            "scheduler_storage_written": False,
            "helper_state_changed": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def build_report(
    *, expected: str, running: str, items: list[dict[str, int]]
) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "HEATER_CANDIDATE_SECRET_CONTRACT_DIAGNOSTIC_COMPLETE",
        "reasons": [],
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": running == expected,
        },
        "contracts": items,
        "summary": {
            "candidate_count": len(items),
            "required_reference_count": sum(item["required_count"] for item in items),
            "resolvable_reference_count": sum(item["resolvable_count"] for item in items),
            "missing_reference_count": sum(item["missing_count"] for item in items),
            "candidate_with_missing_reference_count": sum(
                item["missing_count"] > 0 for item in items
            ),
        },
        "privacy": {
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "raw_yaml_emitted": False,
            "raw_exception_text_emitted": False,
            "private_paths_emitted": False,
        },
        "mutation": {
            "home_assistant_config_written": False,
            "scheduler_service_called": False,
            "scheduler_storage_written": False,
            "helper_state_changed": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose heater candidate secret-contract cardinality privately."
    )
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.diagnose:
        report = blocked_report("DIAGNOSTIC_GATE_REQUIRED")
    else:
        try:
            expected = expected_version()
            running = running_version(args.docker, args.container)
            if running != expected:
                report = blocked_report("HOME_ASSISTANT_VERSION_MISMATCH", expected)
                report["home_assistant"]["running_version"] = running
            else:
                contracts = candidate_contracts()
                validate_contracts(contracts)
                items = sanitize_items(private_probe(args.docker, args.container, contracts))
                if len(items) != len(contracts):
                    raise ValueError("probe candidate count mismatch")
                report = build_report(expected=expected, running=running, items=items)
        except (OSError, RuntimeError, ValueError):
            report = blocked_report("SECRET_CONTRACT_DIAGNOSTIC_RUNTIME_ERROR")

    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report.get("decision") != "BLOCKED" else 20


if __name__ == "__main__":
    raise SystemExit(main())
