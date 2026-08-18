#!/usr/bin/env python3
"""Diagnose candidate heater !secret contract cardinality without emitting aliases."""

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
SECRET_REF = re.compile(r"!secret\s+([^\s#]+)")


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


def candidate_contracts() -> list[list[str]]:
    contracts: list[list[str]] = []
    for path in CANDIDATES:
        text = path.read_text(encoding="utf-8")
        refs = sorted(set(SECRET_REF.findall(text)))
        contracts.append(refs)
    return contracts


def private_probe(docker: str, container: str, contracts: list[list[str]]) -> dict[str, Any]:
    payload = json.dumps(contracts)
    code = r'''
import json
import sys
from pathlib import Path
from homeassistant.util.yaml import Secrets

contracts = json.loads(sys.stdin.read())
secrets = Secrets(Path("/config"))
items = []

for ordinal, aliases in enumerate(contracts, start=1):
    resolvable = 0
    missing = 0
    for alias in aliases:
        try:
            value = secrets.get(alias)
        except Exception:
            missing += 1
        else:
            if value is None:
                missing += 1
            else:
                resolvable += 1
    items.append({
        "ordinal": ordinal,
        "required_count": len(aliases),
        "resolvable_count": resolvable,
        "missing_count": missing,
    })

print(json.dumps({"items": items}, sort_keys=True))
'''
    result = subprocess.run(
        [docker, "exec", "-i", container, "python", "-c", code],
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("private secret contract probe failed")
    decoded = json.loads(result.stdout)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("items"), list):
        raise RuntimeError("private secret contract probe invalid")
    return decoded


def blocked(reason: str, expected: str | None = None) -> dict[str, Any]:
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


def report(expected: str, running: str, items: list[dict[str, Any]]) -> dict[str, Any]:
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
            "candidate_with_missing_reference_count": sum(item["missing_count"] > 0 for item in items),
        },
        "privacy": {
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "raw_yaml_emitted": False,
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.diagnose:
        data = blocked("DIAGNOSTIC_GATE_REQUIRED")
    else:
        try:
            expected = expected_version()
            running = running_version(args.docker, args.container)
            if running != expected:
                data = blocked("HOME_ASSISTANT_VERSION_MISMATCH", expected)
                data["home_assistant"]["running_version"] = running
            else:
                contracts = candidate_contracts()
                private = private_probe(args.docker, args.container, contracts)
                data = report(expected, running, private["items"])
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            data = blocked("SECRET_CONTRACT_DIAGNOSTIC_RUNTIME_ERROR")

    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return 0 if data.get("decision") != "BLOCKED" else 20


if __name__ == "__main__":
    raise SystemExit(main())
