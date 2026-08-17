#!/usr/bin/env python3
"""Plan a byte-preserving Home Assistant YAML dashboard binding activation."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

import yaml

from tools.materialize_majas_dashboard_candidate import (
    DashboardLoader,
    TaggedValue,
    expected_shape,
    load_candidate_tree,
    load_mapping,
    structural_counts,
    validate_with_home_assistant,
)


EXPECTED_HA_VERSION = "2026.8.2"
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"
EXPECTED_CANDIDATE_FILES = {
    "dashboard.yaml",
    "views/00_view.yaml",
    "sections/view_00/00_section.yaml",
    "sections/view_00/10_section.yaml",
    "sections/view_00/20_section.yaml",
}
EXPECTED_CANDIDATE_DIRS = {
    "views",
    "sections",
    "sections/view_00",
}


class ActivationPlanError(RuntimeError):
    """Sanitized activation-plan failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class BindingPlan:
    """Private in-memory binding patch and exact rollback bytes."""

    owner_path: Path
    owner_kind: str
    source_path: Path
    candidate_dashboard: Path
    original_owner_bytes: bytes
    proposed_owner_bytes: bytes
    scalar_start: int
    scalar_end: int
    current_filename: str
    proposed_filename: str


def _bounded_existing_file(config_root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = config_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if resolved != config_root and config_root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


def _bounded_existing_dir(config_root: Path, raw: Path) -> Path:
    candidate = raw
    if not candidate.is_absolute():
        candidate = config_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ActivationPlanError("CANDIDATE_UNAVAILABLE") from exc
    if resolved == config_root or config_root not in resolved.parents:
        raise ActivationPlanError("CANDIDATE_OUTSIDE_CONFIG")
    if not resolved.is_dir():
        raise ActivationPlanError("CANDIDATE_UNAVAILABLE")
    return resolved


def _resolve_lovelace_include(
    config_root: Path,
    value: TaggedValue,
) -> Path:
    if value.tag != "!include":
        raise ActivationPlanError("LOVELACE_MAPPING_UNAVAILABLE")
    include_path = _bounded_existing_file(config_root, value.value)
    if include_path is None:
        raise ActivationPlanError("LOVELACE_INCLUDE_UNRESOLVED")
    return include_path


def resolve_binding_owner(
    config_root: Path,
    dashboard_title: str,
) -> tuple[Path, str, dict[str, Any], str, dict[str, Any], Path]:
    try:
        root = config_root.resolve(strict=True)
    except OSError as exc:
        raise ActivationPlanError("CONFIG_ROOT_UNAVAILABLE") from exc
    if not root.is_dir():
        raise ActivationPlanError("CONFIG_ROOT_UNAVAILABLE")

    configuration_path = root / "configuration.yaml"
    configuration = load_mapping(configuration_path)
    lovelace = configuration.get("lovelace")

    if isinstance(lovelace, TaggedValue):
        owner_path = _resolve_lovelace_include(root, lovelace)
        owner_kind = "LOVELACE_INCLUDE"
        owner_payload = load_mapping(owner_path)
        lovelace_mapping = owner_payload
    elif isinstance(lovelace, dict):
        owner_path = configuration_path
        owner_kind = "CONFIGURATION_ROOT"
        owner_payload = configuration
        lovelace_mapping = lovelace
    else:
        raise ActivationPlanError("LOVELACE_MAPPING_UNAVAILABLE")

    dashboards = lovelace_mapping.get("dashboards")
    if not isinstance(dashboards, dict):
        raise ActivationPlanError("DASHBOARD_REGISTRY_UNAVAILABLE")

    matches: list[tuple[str, dict[str, Any], Path]] = []
    for key, definition in dashboards.items():
        if not isinstance(key, str) or not isinstance(definition, dict):
            continue
        if definition.get("title") != dashboard_title:
            continue
        source_path = _bounded_existing_file(
            root,
            definition.get("filename"),
        )
        if source_path is not None:
            matches.append((key, definition, source_path))

    if len(matches) != 1:
        raise ActivationPlanError("DASHBOARD_BINDING_NOT_UNIQUE")

    dashboard_key, definition, source_path = matches[0]
    return (
        owner_path,
        owner_kind,
        owner_payload,
        dashboard_key,
        definition,
        source_path,
    )


def _mapping_value(node: yaml.Node, key: str) -> yaml.Node:
    if not isinstance(node, yaml.MappingNode):
        raise ActivationPlanError("BINDING_NODE_UNAVAILABLE")
    matches = [
        value_node
        for key_node, value_node in node.value
        if isinstance(key_node, yaml.ScalarNode)
        and key_node.value == key
    ]
    if len(matches) != 1:
        raise ActivationPlanError("BINDING_NODE_UNAVAILABLE")
    return matches[0]


def _find_filename_scalar(
    owner_text: str,
    owner_kind: str,
    dashboard_key: str,
) -> yaml.ScalarNode:
    try:
        root_node = yaml.compose(owner_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ActivationPlanError("BINDING_NODE_UNAVAILABLE") from exc
    if root_node is None:
        raise ActivationPlanError("BINDING_NODE_UNAVAILABLE")

    current = root_node
    if owner_kind == "CONFIGURATION_ROOT":
        current = _mapping_value(current, "lovelace")
    current = _mapping_value(current, "dashboards")
    current = _mapping_value(current, dashboard_key)
    current = _mapping_value(current, "filename")
    if not isinstance(current, yaml.ScalarNode):
        raise ActivationPlanError("BINDING_NODE_UNAVAILABLE")
    return current


def _format_scalar(value: str, style: str | None) -> str:
    if style == "'":
        return "'" + value.replace("'", "''") + "'"
    if style == '"':
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'
    if any(char in value for char in "\n\r\t:#{}[],&*!|>'\"%@`"):
        rendered = yaml.safe_dump(
            value,
            default_flow_style=True,
            allow_unicode=True,
        ).strip()
        return rendered.removesuffix("...").rstrip()
    if not value or value[0] in "-? " or value[-1].isspace():
        return yaml.safe_dump(
            value,
            default_flow_style=True,
            allow_unicode=True,
        ).splitlines()[0]
    return value


def _candidate_tree_ok(candidate_root: Path) -> bool:
    entries = list(candidate_root.rglob("*"))
    if any(item.is_symlink() for item in entries):
        return False
    files = {
        item.relative_to(candidate_root).as_posix()
        for item in entries
        if item.is_file()
    }
    dirs = {
        item.relative_to(candidate_root).as_posix()
        for item in entries
        if item.is_dir()
    }
    return files == EXPECTED_CANDIDATE_FILES and dirs == EXPECTED_CANDIDATE_DIRS


def build_binding_plan(
    *,
    config_root: Path,
    dashboard_title: str,
    candidate_root: Path,
) -> BindingPlan:
    root = config_root.resolve(strict=True)
    candidate = _bounded_existing_dir(root, candidate_root)
    if not _candidate_tree_ok(candidate):
        raise ActivationPlanError("CANDIDATE_TREE_MISMATCH")

    (
        owner_path,
        owner_kind,
        owner_payload,
        dashboard_key,
        definition,
        source_path,
    ) = resolve_binding_owner(root, dashboard_title)

    current_filename = definition.get("filename")
    if not isinstance(current_filename, str):
        raise ActivationPlanError("CURRENT_FILENAME_INVALID")

    candidate_dashboard = (candidate / "dashboard.yaml").resolve(strict=True)
    if candidate_dashboard == source_path:
        raise ActivationPlanError("CANDIDATE_ALREADY_ACTIVE")
    proposed_filename = candidate_dashboard.relative_to(root).as_posix()

    try:
        owner_text = owner_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ActivationPlanError("OWNER_FILE_UNAVAILABLE") from exc

    scalar = _find_filename_scalar(owner_text, owner_kind, dashboard_key)
    if scalar.value != current_filename:
        raise ActivationPlanError("BINDING_SCALAR_DRIFT")

    replacement = _format_scalar(proposed_filename, scalar.style)
    char_start = scalar.start_mark.index
    char_end = scalar.end_mark.index
    proposed_text = (
        owner_text[:char_start]
        + replacement
        + owner_text[char_end:]
    )

    original_bytes = owner_text.encode("utf-8")
    proposed_bytes = proposed_text.encode("utf-8")
    start = len(owner_text[:char_start].encode("utf-8"))
    end = len(owner_text[:char_end].encode("utf-8"))

    try:
        proposed_payload = yaml.load(
            proposed_text,
            Loader=DashboardLoader,
        )
    except yaml.YAMLError as exc:
        raise ActivationPlanError("PROPOSED_OWNER_PARSE_FAILED") from exc
    if not isinstance(proposed_payload, dict):
        raise ActivationPlanError("PROPOSED_OWNER_PARSE_FAILED")

    expected_payload = copy.deepcopy(owner_payload)
    target_lovelace = (
        expected_payload.get("lovelace")
        if owner_kind == "CONFIGURATION_ROOT"
        else expected_payload
    )
    if not isinstance(target_lovelace, dict):
        raise ActivationPlanError("PROPOSED_OWNER_PARSE_FAILED")
    target_dashboards = target_lovelace.get("dashboards")
    if not isinstance(target_dashboards, dict):
        raise ActivationPlanError("PROPOSED_OWNER_PARSE_FAILED")
    target_definition = target_dashboards.get(dashboard_key)
    if not isinstance(target_definition, dict):
        raise ActivationPlanError("PROPOSED_OWNER_PARSE_FAILED")
    target_definition["filename"] = proposed_filename

    if proposed_payload != expected_payload:
        raise ActivationPlanError("NON_TARGET_SEMANTIC_DRIFT")

    proposed_suffix = proposed_bytes[
        start + len(replacement.encode("utf-8")) :
    ]
    if (
        proposed_bytes[:start] != original_bytes[:start]
        or proposed_suffix != original_bytes[end:]
    ):
        raise ActivationPlanError("NON_TARGET_BYTE_DRIFT")

    return BindingPlan(
        owner_path=owner_path,
        owner_kind=owner_kind,
        source_path=source_path,
        candidate_dashboard=candidate_dashboard,
        original_owner_bytes=original_bytes,
        proposed_owner_bytes=proposed_bytes,
        scalar_start=start,
        scalar_end=end,
        current_filename=current_filename,
        proposed_filename=proposed_filename,
    )


VersionValidator = Callable[[Path, Path, str], dict[str, bool]]


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": [reason],
        "validation": {
            "active_candidate_equivalent": False,
            "candidate_tree_exact": False,
            "binding_patch_unique": False,
            "non_target_bytes_preserved": False,
            "non_target_semantics_preserved": False,
            "home_assistant_version_match": False,
            "home_assistant_candidate_parses": False,
        },
        "rollback": {
            "exact_original_owner_bytes_captured_in_memory": False,
        },
        "privacy": {
            "raw_private_values_emitted": False,
            "private_paths_emitted": False,
            "binding_values_emitted": False,
        },
        "mutation": {
            "owner_file_modified": False,
            "candidate_tree_modified": False,
            "active_dashboard_modified": False,
            "live_dashboard_binding_changed": False,
            "storage_write": False,
            "reload_or_restart": False,
        },
    }


def plan_activation(
    *,
    config_root: Path,
    dashboard_title: str,
    candidate_root: Path,
    expected_version: str = EXPECTED_HA_VERSION,
    validator: VersionValidator = validate_with_home_assistant,
) -> dict[str, Any]:
    try:
        root = config_root.resolve(strict=True)
        plan = build_binding_plan(
            config_root=root,
            dashboard_title=dashboard_title,
            candidate_root=candidate_root,
        )

        active_payload = load_mapping(plan.source_path)
        candidate_payload = load_candidate_tree(
            plan.candidate_dashboard.parent
        )
        active_counts = structural_counts(active_payload)
        candidate_counts = structural_counts(candidate_payload)

        if not expected_shape(active_counts):
            raise ActivationPlanError("ACTIVE_STRUCTURE_MISMATCH")
        if not expected_shape(candidate_counts):
            raise ActivationPlanError("CANDIDATE_STRUCTURE_MISMATCH")
        if active_payload != candidate_payload:
            raise ActivationPlanError("CANDIDATE_NOT_EQUIVALENT")

        ha_result = validator(
            plan.candidate_dashboard,
            root,
            expected_version,
        )
        if not (
            ha_result.get("version_match") is True
            and ha_result.get("candidate_parses") is True
        ):
            raise ActivationPlanError("HOME_ASSISTANT_VALIDATION_FAILED")

        return {
            "schema": 1,
            "decision": "READY_FOR_PRIVATE_ACTIVATION_DRY_RUN",
            "reasons": [],
            "owner": {
                "kind": plan.owner_kind,
                "single_filename_scalar_patch": True,
            },
            "structure": {
                "active": active_counts,
                "candidate": candidate_counts,
            },
            "validation": {
                "active_candidate_equivalent": True,
                "candidate_tree_exact": True,
                "binding_patch_unique": True,
                "non_target_bytes_preserved": True,
                "non_target_semantics_preserved": True,
                "home_assistant_version_match": True,
                "home_assistant_candidate_parses": True,
            },
            "rollback": {
                "exact_original_owner_bytes_captured_in_memory": True,
            },
            "privacy": {
                "raw_private_values_emitted": False,
                "private_paths_emitted": False,
                "binding_values_emitted": False,
            },
            "mutation": {
                "owner_file_modified": False,
                "candidate_tree_modified": False,
                "active_dashboard_modified": False,
                "live_dashboard_binding_changed": False,
                "storage_write": False,
                "reload_or_restart": False,
            },
        }
    except ActivationPlanError as exc:
        return blocked_report(exc.reason)
    except (OSError, UnicodeError, ValueError):
        return blocked_report("ACTIVATION_PLAN_FAILED")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan a byte-preserving private Mājas dashboard binding "
            "activation. The tool never writes live configuration."
        )
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=DEFAULT_CONFIG_ROOT,
    )
    parser.add_argument(
        "--dashboard-title",
        default=DEFAULT_DASHBOARD_TITLE,
    )
    parser.add_argument(
        "--candidate-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--expected-version",
        default=EXPECTED_HA_VERSION,
    )
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.plan:
        report = blocked_report("PLAN_GATE_REQUIRED")
    else:
        report = plan_activation(
            config_root=args.config_root,
            dashboard_title=args.dashboard_title,
            candidate_root=args.candidate_root,
            expected_version=args.expected_version,
        )
    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    return (
        0
        if report.get("decision")
        == "READY_FOR_PRIVATE_ACTIVATION_DRY_RUN"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
