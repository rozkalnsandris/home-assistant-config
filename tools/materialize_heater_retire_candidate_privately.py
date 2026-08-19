#!/usr/bin/env python3
"""Materialize the heater RETIRE candidate without exposing private bindings."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
SECRET_VALUE = re.compile(r"!secret\s+[A-Za-z0-9_-]+")
INITIAL_SECRET_LINE = re.compile(
    r"(?m)^(?P<prefix>\s*initial:\s*)!secret\s+[A-Za-z0-9_-]+\s*$"
)
BINDING_VARIABLE_SECRET_LINE = re.compile(
    r"(?m)^(?P<prefix>\s*heater_entity:\s*)!secret\s+[A-Za-z0-9_-]+\s*$"
)

EXPECTED_FIRST_SECRET_COUNT = 3
EXPECTED_SECOND_SECRET_COUNT = 2
NEUTRAL_RETIRE_INITIAL = '"00:00:00"'
READY = "PRIVATE_RETIRE_CANDIDATE_MATERIALIZED"
BLOCKED = "BLOCKED"


class MaterializationError(RuntimeError):
    """Fail-closed private materialization error with a public-safe reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _entity_scalar(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if ENTITY_ID.fullmatch(value) else None


def _automation_by_id(config: Any, automation_id: str) -> dict[str, Any] | None:
    automations = config.get("automation") if isinstance(config, dict) else None
    if not isinstance(automations, list):
        return None
    matches = [
        item
        for item in automations
        if isinstance(item, dict) and item.get("id") == automation_id
    ]
    return matches[0] if len(matches) == 1 else None


def _action_target(automation: Any, service: str) -> str | None:
    if not isinstance(automation, dict):
        return None
    actions = automation.get("action")
    if not isinstance(actions, list):
        return None

    matches: list[str] = []
    for action in actions:
        if not isinstance(action, dict) or action.get("service") != service:
            continue
        target = action.get("target")
        if not isinstance(target, dict):
            continue
        value = _entity_scalar(target.get("entity_id"))
        if value is not None:
            matches.append(value)

    return matches[0] if len(matches) == 1 else None


def extract_live_target(config: Any) -> str | None:
    """Resolve the bounded legacy ON/OFF/timer target only when all are equal."""
    on_target = _action_target(
        _automation_by_id(config, "silditajs_grafiks_on"),
        "switch.turn_on",
    )
    off_target = _action_target(
        _automation_by_id(config, "silditajs_grafiks_off"),
        "switch.turn_off",
    )
    timer_target = _action_target(
        _automation_by_id(config, "silditajs_auto_off"),
        "switch.turn_off",
    )

    values = (on_target, off_target, timer_target)
    if any(value is None for value in values):
        return None

    assert on_target is not None
    assert off_target is not None
    assert timer_target is not None

    if not hmac.compare_digest(on_target, off_target):
        return None
    if not hmac.compare_digest(on_target, timer_target):
        return None
    return on_target


def _replace_binding_secret_lines(text: str, target_literal: str) -> str:
    return SECRET_VALUE.sub(target_literal, text)


def materialize_candidate_texts(
    first: str,
    second: str,
    private_target: str,
) -> tuple[str, str, dict[str, int]]:
    """Materialize exact bounded secret placeholders for the RETIRE path."""
    target = _entity_scalar(private_target)
    if target is None:
        raise MaterializationError("PRIVATE_TARGET_SHAPE_INVALID")

    first_secret_count = len(SECRET_VALUE.findall(first))
    second_secret_count = len(SECRET_VALUE.findall(second))
    initial_matches = list(INITIAL_SECRET_LINE.finditer(second))
    binding_matches = list(BINDING_VARIABLE_SECRET_LINE.finditer(second))

    if first_secret_count != EXPECTED_FIRST_SECRET_COUNT:
        raise MaterializationError("FIRST_CANDIDATE_SECRET_SHAPE_CHANGED")
    if second_secret_count != EXPECTED_SECOND_SECRET_COUNT:
        raise MaterializationError("SECOND_CANDIDATE_SECRET_SHAPE_CHANGED")
    if len(initial_matches) != 1:
        raise MaterializationError("RETIRE_INITIAL_PLACEHOLDER_SHAPE_CHANGED")
    if len(binding_matches) != 1:
        raise MaterializationError("SECOND_BINDING_PLACEHOLDER_SHAPE_CHANGED")

    target_literal = json.dumps(target)
    first_materialized = _replace_binding_secret_lines(first, target_literal)

    second_materialized, initial_count = INITIAL_SECRET_LINE.subn(
        lambda match: f"{match.group('prefix')}{NEUTRAL_RETIRE_INITIAL}",
        second,
    )
    second_materialized, binding_count = BINDING_VARIABLE_SECRET_LINE.subn(
        lambda match: f"{match.group('prefix')}{target_literal}",
        second_materialized,
    )

    if initial_count != 1 or binding_count != 1:
        raise MaterializationError("SECOND_CANDIDATE_REPLACEMENT_FAILED")
    if SECRET_VALUE.search(first_materialized) or SECRET_VALUE.search(second_materialized):
        raise MaterializationError("UNMATERIALIZED_SECRET_PLACEHOLDER_REMAINS")

    return (
        first_materialized,
        second_materialized,
        {
            "first_binding_placeholder_count": first_secret_count,
            "second_binding_placeholder_count": binding_count,
            "retire_initial_placeholder_count": initial_count,
            "total_binding_placeholder_count": first_secret_count + binding_count,
        },
    )


def _require_regular_file(path: Path, reason: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise MaterializationError(reason) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise MaterializationError(reason)


def _require_private_tmp_directory(path: Path) -> Path:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MaterializationError("PRIVATE_OUTPUT_DIRECTORY_INVALID") from exc

    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise MaterializationError("PRIVATE_OUTPUT_DIRECTORY_INVALID")
    if resolved == Path("/tmp") or Path("/tmp") not in resolved.parents:
        raise MaterializationError("PRIVATE_OUTPUT_DIRECTORY_NOT_TEMPORARY")
    return resolved


def _write_private_new(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _load_yaml_dict(path: Path, secrets_root: Path) -> dict[str, Any]:
    try:
        from homeassistant.util.yaml import Secrets, load_yaml_dict
    except ImportError as exc:
        raise MaterializationError("HOME_ASSISTANT_YAML_LOADER_UNAVAILABLE") from exc

    try:
        loaded = load_yaml_dict(str(path), Secrets(secrets_root))
    except Exception as exc:
        raise MaterializationError("HOME_ASSISTANT_YAML_LOAD_FAILED") from exc
    if not isinstance(loaded, dict):
        raise MaterializationError("HOME_ASSISTANT_YAML_TOP_LEVEL_INVALID")
    return loaded


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": BLOCKED,
        "reasons": [reason],
        "binding": {
            "legacy_target_count_expected": 3,
            "legacy_targets_resolved_and_equal": False,
            "private_target_emitted": False,
            "private_target_hashed": False,
        },
        "materialization": {
            "retire_mode": True,
            "neutral_initial_used": False,
            "output_files_written_count": 0,
            "materialized_yaml_reload_passed": False,
        },
        "privacy": {
            "entity_ids_or_targets_emitted": False,
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "binding_hashes_emitted": False,
            "raw_yaml_emitted": False,
            "private_paths_emitted": False,
            "latent_schedule_values_emitted": False,
        },
        "production_mutation": {
            "home_assistant_config_written": False,
            "scheduler_service_called": False,
            "scheduler_storage_written": False,
            "helper_state_changed": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def ready_report(counts: dict[str, int]) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": READY,
        "reasons": [],
        "binding": {
            "legacy_target_count_expected": 3,
            "legacy_targets_resolved_and_equal": True,
            "private_target_emitted": False,
            "private_target_hashed": False,
        },
        "materialization": {
            "retire_mode": True,
            "neutral_initial_used": True,
            "output_files_written_count": 2,
            "materialized_yaml_reload_passed": True,
            **counts,
        },
        "privacy": {
            "entity_ids_or_targets_emitted": False,
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "binding_hashes_emitted": False,
            "raw_yaml_emitted": False,
            "private_paths_emitted": False,
            "latent_schedule_values_emitted": False,
        },
        "production_mutation": {
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
        description="Materialize a private heater RETIRE candidate in container temp storage."
    )
    parser.add_argument("--config-root", type=Path, default=Path("/config"))
    parser.add_argument("--live-legacy", type=Path)
    parser.add_argument("--candidate-one", type=Path)
    parser.add_argument("--candidate-two", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--retire", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.materialize or not args.retire:
        report = blocked_report("PRIVATE_RETIRE_MATERIALIZATION_GATE_REQUIRED")
    elif any(
        value is None
        for value in (
            args.live_legacy,
            args.candidate_one,
            args.candidate_two,
            args.output_dir,
        )
    ):
        report = blocked_report("PRIVATE_RETIRE_MATERIALIZATION_ARGUMENTS_REQUIRED")
    else:
        output_paths: list[Path] = []
        try:
            assert args.live_legacy is not None
            assert args.candidate_one is not None
            assert args.candidate_two is not None
            assert args.output_dir is not None

            _require_regular_file(args.live_legacy, "LIVE_LEGACY_FILE_INVALID")
            _require_regular_file(args.candidate_one, "FIRST_CANDIDATE_FILE_INVALID")
            _require_regular_file(args.candidate_two, "SECOND_CANDIDATE_FILE_INVALID")
            output_dir = _require_private_tmp_directory(args.output_dir)

            live = _load_yaml_dict(args.live_legacy, args.config_root)
            private_target = extract_live_target(live)
            if private_target is None:
                raise MaterializationError("PRIVATE_LIVE_TARGETS_NOT_EQUAL_OR_UNRESOLVED")

            first = args.candidate_one.read_text(encoding="utf-8")
            second = args.candidate_two.read_text(encoding="utf-8")
            first_out, second_out, counts = materialize_candidate_texts(
                first,
                second,
                private_target,
            )

            first_path = output_dir / "candidate-1.yaml"
            second_path = output_dir / "candidate-2.yaml"
            _write_private_new(first_path, first_out)
            output_paths.append(first_path)
            _write_private_new(second_path, second_out)
            output_paths.append(second_path)

            _load_yaml_dict(first_path, args.config_root)
            _load_yaml_dict(second_path, args.config_root)
            report = ready_report(counts)
        except (MaterializationError, OSError, UnicodeError) as exc:
            for path in output_paths:
                try:
                    path.unlink()
                except OSError:
                    pass
            reason = (
                exc.reason
                if isinstance(exc, MaterializationError)
                else "PRIVATE_RETIRE_MATERIALIZATION_RUNTIME_ERROR"
            )
            report = blocked_report(reason)

    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report.get("decision") == READY else 20


if __name__ == "__main__":
    raise SystemExit(main())
