#!/usr/bin/env python3
"""Prepare one rollback-first Sections flattening byte plan without writing live config."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml

from tools.materialize_majas_dashboard_candidate import (
    DashboardLoader,
    TaggedValue,
    load_candidate_tree,
    load_mapping,
    structural_counts,
)
from tools.plan_majas_dashboard_activation import (
    EXPECTED_CANDIDATE_DIRS,
    EXPECTED_CANDIDATE_FILES,
    resolve_binding_owner,
)
from tools.plan_majas_sections_modernization import (
    EXPECTED_AFTER_STRUCTURE,
    analyze_flattening_plan,
    running_home_assistant_version,
)

EXPECTED_HA_VERSION = "2026.8.2"
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"
READY_DECISION = "READY_FOR_OWNER_AUTHORIZATION_FOR_ONE_SHOT_SECTIONS_FLATTENING"


class SectionsFlatteningApplyPlanError(RuntimeError):
    """Sanitized apply-preparation failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SectionsFlatteningBytePlan:
    """Private in-memory preimage/postimage for one bounded target-file patch."""

    owner_path: Path
    owner_kind: str
    target_section_path: Path
    original_owner_bytes: bytes
    original_target_bytes: bytes
    proposed_target_bytes: bytes
    target_span_start: int
    target_span_end: int
    planner_report: dict[str, Any]


def privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "entity_ids_emitted": False,
        "view_section_card_titles_emitted": False,
        "card_type_names_emitted": False,
        "custom_card_type_names_emitted": False,
        "actions_or_urls_emitted": False,
        "binding_values_emitted": False,
        "private_byte_content_emitted": False,
    }


def mutation_report() -> dict[str, bool]:
    return {
        "owner_file_modified": False,
        "dashboard_modified": False,
        "candidate_tree_modified": False,
        "old_source_modified": False,
        "storage_write": False,
        "binding_changed": False,
        "grid_options_modified": False,
        "reload_or_restart": False,
    }


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": [reason],
        "privacy": privacy_report(),
        "mutation": mutation_report(),
    }


def private_review_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "NEEDS_PRIVATE_REVIEW",
        "reasons": [reason],
        "privacy": privacy_report(),
        "mutation": mutation_report(),
    }


def _active_tree_inventory(active_root: Path) -> tuple[set[str], set[str]]:
    entries = list(active_root.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise SectionsFlatteningApplyPlanError("ACTIVE_TREE_SYMLINK_PRESENT")
    files = {
        item.relative_to(active_root).as_posix()
        for item in entries
        if item.is_file()
    }
    dirs = {
        item.relative_to(active_root).as_posix()
        for item in entries
        if item.is_dir()
    }
    unexpected = [
        item
        for item in entries
        if not item.is_file() and not item.is_dir()
    ]
    if unexpected:
        raise SectionsFlatteningApplyPlanError("ACTIVE_TREE_UNEXPECTED_ENTRY")
    if files != EXPECTED_CANDIDATE_FILES or dirs != EXPECTED_CANDIDATE_DIRS:
        raise SectionsFlatteningApplyPlanError("ACTIVE_TREE_MISMATCH")
    return files, dirs


def _bounded_include_dir(
    *,
    active_root: Path,
    source_file: Path,
    value: Any,
) -> Path:
    if (
        not isinstance(value, TaggedValue)
        or value.tag != "!include_dir_list"
        or not isinstance(value.value, str)
        or not value.value.strip()
    ):
        raise SectionsFlatteningApplyPlanError("INCLUDE_DIR_LIST_REQUIRED")
    try:
        directory = (source_file.parent / value.value).resolve(strict=True)
    except OSError as exc:
        raise SectionsFlatteningApplyPlanError("INCLUDE_DIR_UNAVAILABLE") from exc
    if directory == active_root or active_root not in directory.parents:
        raise SectionsFlatteningApplyPlanError("INCLUDE_DIR_OUTSIDE_ACTIVE_TREE")
    if not directory.is_dir():
        raise SectionsFlatteningApplyPlanError("INCLUDE_DIR_UNAVAILABLE")
    return directory


def _yaml_files(directory: Path) -> list[Path]:
    return sorted(
        (item for item in directory.rglob("*.yaml") if item.is_file()),
        key=lambda item: item.relative_to(directory).as_posix(),
    )


def _mapping_value_node(node: yaml.Node, key: str) -> yaml.Node:
    if not isinstance(node, yaml.MappingNode):
        raise SectionsFlatteningApplyPlanError("TARGET_YAML_NODE_UNAVAILABLE")
    matches = [
        value
        for key_node, value in node.value
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key
    ]
    if len(matches) != 1:
        raise SectionsFlatteningApplyPlanError("TARGET_YAML_NODE_UNAVAILABLE")
    return matches[0]


def _line_start(text: str, index: int) -> int:
    newline = text.rfind("\n", 0, index)
    return 0 if newline < 0 else newline + 1


def _item_line_info(text: str, node: yaml.Node) -> tuple[int, str]:
    start = _line_start(text, node.start_mark.index)
    prefix = text[start : node.start_mark.index]
    if not prefix.endswith("- "):
        raise SectionsFlatteningApplyPlanError("SEQUENCE_ITEM_LAYOUT_UNSUPPORTED")
    indent = prefix[:-2]
    if indent.strip():
        raise SectionsFlatteningApplyPlanError("SEQUENCE_ITEM_LAYOUT_UNSUPPORTED")
    return start, indent


def _item_span(
    text: str,
    sequence: yaml.SequenceNode,
    index: int,
) -> tuple[int, int, str]:
    if index < 0 or index >= len(sequence.value):
        raise SectionsFlatteningApplyPlanError("TARGET_CARD_ORDINAL_DRIFT")
    node = sequence.value[index]
    start, indent = _item_line_info(text, node)
    if index + 1 < len(sequence.value):
        end = _line_start(text, sequence.value[index + 1].start_mark.index)
    else:
        end = node.end_mark.index
    if end <= start:
        raise SectionsFlatteningApplyPlanError("TARGET_BYTE_SPAN_INVALID")
    return start, end, indent


def _dedent_child_block(raw: str, *, delta: int) -> str:
    if delta <= 0:
        raise SectionsFlatteningApplyPlanError("CHILD_INDENTATION_INVALID")
    rendered: list[str] = []
    prefix = " " * delta
    for line in raw.splitlines(keepends=True):
        if not line.strip():
            rendered.append(line[delta:] if line.startswith(prefix) else line)
            continue
        if not line.startswith(prefix):
            raise SectionsFlatteningApplyPlanError("CHILD_INDENTATION_INVALID")
        rendered.append(line[delta:])
    return "".join(rendered)


def _replacement_from_child_source(
    *,
    text: str,
    wrapper_node: yaml.MappingNode,
    target_indent: str,
) -> str:
    child_sequence = _mapping_value_node(wrapper_node, "cards")
    if not isinstance(child_sequence, yaml.SequenceNode):
        raise SectionsFlatteningApplyPlanError("GRID_WRAPPER_CHILD_SEQUENCE_REQUIRED")
    if len(child_sequence.value) != 4:
        raise SectionsFlatteningApplyPlanError("GRID_WRAPPER_CHILD_COUNT_MISMATCH")

    blocks: list[str] = []
    child_indent: str | None = None
    for index, child_node in enumerate(child_sequence.value):
        start, end, indent = _item_span(text, child_sequence, index)
        if child_indent is None:
            child_indent = indent
        elif indent != child_indent:
            raise SectionsFlatteningApplyPlanError("CHILD_INDENTATION_INVALID")
        if not indent.startswith(target_indent) or len(indent) <= len(target_indent):
            raise SectionsFlatteningApplyPlanError("CHILD_INDENTATION_INVALID")
        delta = len(indent) - len(target_indent)
        blocks.append(
            _dedent_child_block(
                text[start:end],
                delta=delta,
            )
        )
    return "".join(blocks)


def _resolve_target_section(
    *,
    active_root: Path,
    active_dashboard: Path,
    expanded_payload: dict[str, Any],
    view_index: int,
    section_index: int,
) -> tuple[Path, dict[str, Any]]:
    expected_dashboard = (active_root / "dashboard.yaml").resolve(strict=True)
    if active_dashboard.resolve(strict=True) != expected_dashboard:
        raise SectionsFlatteningApplyPlanError("ACTIVE_DASHBOARD_ROOT_MISMATCH")

    dashboard_mapping = load_mapping(active_dashboard)
    views_dir = _bounded_include_dir(
        active_root=active_root,
        source_file=active_dashboard,
        value=dashboard_mapping.get("views"),
    )
    view_files = _yaml_files(views_dir)
    if len(view_files) != 1 or view_index >= len(view_files):
        raise SectionsFlatteningApplyPlanError("VIEW_SOURCE_NOT_UNIQUE")
    view_file = view_files[view_index]

    view_mapping = load_mapping(view_file)
    sections_dir = _bounded_include_dir(
        active_root=active_root,
        source_file=view_file,
        value=view_mapping.get("sections"),
    )
    section_files = _yaml_files(sections_dir)
    if len(section_files) != 3 or section_index >= len(section_files):
        raise SectionsFlatteningApplyPlanError("SECTION_SOURCE_NOT_UNIQUE")
    target_section_path = section_files[section_index]

    section_mapping = load_mapping(target_section_path)
    try:
        expanded_section = expanded_payload["views"][view_index]["sections"][section_index]
    except (KeyError, IndexError, TypeError) as exc:
        raise SectionsFlatteningApplyPlanError("EXPANDED_TARGET_SECTION_UNAVAILABLE") from exc
    if section_mapping != expanded_section:
        raise SectionsFlatteningApplyPlanError("TARGET_SECTION_SOURCE_DRIFT")
    return target_section_path, section_mapping


def _build_target_bytes(
    *,
    target_section_path: Path,
    section_payload: dict[str, Any],
    card_index: int,
) -> tuple[bytes, bytes, int, int]:
    try:
        original_text = target_section_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SectionsFlatteningApplyPlanError("TARGET_SOURCE_UNAVAILABLE") from exc

    try:
        root_node = yaml.compose(original_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise SectionsFlatteningApplyPlanError("TARGET_YAML_NODE_UNAVAILABLE") from exc
    if not isinstance(root_node, yaml.MappingNode):
        raise SectionsFlatteningApplyPlanError("TARGET_YAML_NODE_UNAVAILABLE")

    cards_node = _mapping_value_node(root_node, "cards")
    if not isinstance(cards_node, yaml.SequenceNode):
        raise SectionsFlatteningApplyPlanError("TARGET_CARD_SEQUENCE_REQUIRED")

    start, end, target_indent = _item_span(original_text, cards_node, card_index)
    wrapper_node = cards_node.value[card_index]
    if not isinstance(wrapper_node, yaml.MappingNode):
        raise SectionsFlatteningApplyPlanError("GRID_WRAPPER_MAPPING_REQUIRED")

    replacement = _replacement_from_child_source(
        text=original_text,
        wrapper_node=wrapper_node,
        target_indent=target_indent,
    )
    proposed_text = original_text[:start] + replacement + original_text[end:]

    try:
        proposed_section = yaml.load(proposed_text, Loader=DashboardLoader)
    except yaml.YAMLError as exc:
        raise SectionsFlatteningApplyPlanError("PROPOSED_TARGET_PARSE_FAILED") from exc
    if not isinstance(proposed_section, dict):
        raise SectionsFlatteningApplyPlanError("PROPOSED_TARGET_PARSE_FAILED")

    cards = section_payload.get("cards")
    if not isinstance(cards, list) or card_index >= len(cards):
        raise SectionsFlatteningApplyPlanError("TARGET_CARD_ORDINAL_DRIFT")
    wrapper = cards[card_index]
    if not isinstance(wrapper, dict):
        raise SectionsFlatteningApplyPlanError("GRID_WRAPPER_MAPPING_REQUIRED")
    children = wrapper.get("cards")
    if not isinstance(children, list) or len(children) != 4:
        raise SectionsFlatteningApplyPlanError("GRID_WRAPPER_CHILD_COUNT_MISMATCH")

    expected_section = copy.deepcopy(section_payload)
    expected_cards = expected_section["cards"]
    expected_cards[card_index : card_index + 1] = copy.deepcopy(children)
    if proposed_section != expected_section:
        raise SectionsFlatteningApplyPlanError("PROPOSED_TARGET_SEMANTIC_DRIFT")

    original_bytes = original_text.encode("utf-8")
    proposed_bytes = proposed_text.encode("utf-8")
    byte_start = len(original_text[:start].encode("utf-8"))
    byte_end = len(original_text[:end].encode("utf-8"))
    replacement_bytes = replacement.encode("utf-8")

    if proposed_bytes[:byte_start] != original_bytes[:byte_start]:
        raise SectionsFlatteningApplyPlanError("NON_TARGET_PREFIX_BYTE_DRIFT")
    proposed_suffix = proposed_bytes[byte_start + len(replacement_bytes) :]
    if proposed_suffix != original_bytes[byte_end:]:
        raise SectionsFlatteningApplyPlanError("NON_TARGET_SUFFIX_BYTE_DRIFT")

    return original_bytes, proposed_bytes, byte_start, byte_end


def prepare_live_apply(
    *,
    config_root: Path,
    dashboard_title: str,
    expected_version: str,
    running_version: str,
) -> tuple[SectionsFlatteningBytePlan | None, dict[str, Any]]:
    """Prepare one private byte plan and return only sanitized public evidence."""

    if running_version != expected_version:
        return None, blocked_report("HOME_ASSISTANT_VERSION_MISMATCH")

    try:
        root = config_root.resolve(strict=True)
        (
            owner_path,
            owner_kind,
            _owner_payload,
            _dashboard_key,
            _definition,
            active_dashboard,
        ) = resolve_binding_owner(root, dashboard_title)

        active_root = active_dashboard.parent.resolve(strict=True)
        files, dirs = _active_tree_inventory(active_root)
        expanded_payload = load_candidate_tree(active_root)
        planner = analyze_flattening_plan(expanded_payload)

        if planner.get("decision") == "NEEDS_PRIVATE_REVIEW":
            reasons = planner.get("reasons")
            reason = (
                reasons[0]
                if isinstance(reasons, list) and reasons and isinstance(reasons[0], str)
                else "PRIVATE_REVIEW_REQUIRED"
            )
            return None, private_review_report(reason)
        if planner.get("decision") != "READY_FOR_PRIVATE_SECTIONS_FLATTENING_DRY_RUN":
            raise SectionsFlatteningApplyPlanError("SECTIONS_PLANNER_NOT_READY")

        plan = planner.get("plan")
        if not isinstance(plan, dict):
            raise SectionsFlatteningApplyPlanError("SECTIONS_PLAN_SHAPE_UNAVAILABLE")
        view_index = plan.get("target_view_ordinal")
        section_index = plan.get("target_section_ordinal")
        card_index = plan.get("target_card_ordinal")
        if not all(isinstance(value, int) for value in (view_index, section_index, card_index)):
            raise SectionsFlatteningApplyPlanError("TARGET_ORDINALS_UNAVAILABLE")

        target_section_path, section_payload = _resolve_target_section(
            active_root=active_root,
            active_dashboard=active_dashboard,
            expanded_payload=expanded_payload,
            view_index=view_index,
            section_index=section_index,
        )
        original_target, proposed_target, span_start, span_end = _build_target_bytes(
            target_section_path=target_section_path,
            section_payload=section_payload,
            card_index=card_index,
        )

        proposed_payload = copy.deepcopy(expanded_payload)
        target_section = proposed_payload["views"][view_index]["sections"][section_index]
        target_cards = target_section["cards"]
        wrapper = target_cards[card_index]
        children = wrapper["cards"]
        target_cards[card_index : card_index + 1] = copy.deepcopy(children)

        if structural_counts(proposed_payload) != EXPECTED_AFTER_STRUCTURE:
            raise SectionsFlatteningApplyPlanError("PROPOSED_STRUCTURE_MISMATCH")

        owner_bytes = owner_path.read_bytes()
        byte_plan = SectionsFlatteningBytePlan(
            owner_path=owner_path,
            owner_kind=owner_kind,
            target_section_path=target_section_path,
            original_owner_bytes=owner_bytes,
            original_target_bytes=original_target,
            proposed_target_bytes=proposed_target,
            target_span_start=span_start,
            target_span_end=span_end,
            planner_report=planner,
        )

        report = {
            "schema": 1,
            "decision": READY_DECISION,
            "reasons": [],
            "binding": {
                "resolved": True,
                "owner_kind": owner_kind,
                "binding_change_planned": False,
            },
            "structure": planner["structure"],
            "byte_plan": {
                "active_tree_exact": True,
                "active_regular_files": len(files),
                "active_directories": len(dirs),
                "target_source_file_unique": True,
                "target_byte_span_unique": True,
                "outside_target_span_bytes_preserved": True,
                "proposed_target_semantics_match_expected": True,
                "owner_preimage_bytes_captured_in_memory": True,
                "target_preimage_bytes_captured_in_memory": True,
                "target_postimage_bytes_captured_in_memory": True,
                "byte_exact_rollback_input_available": True,
                "single_target_file_write_surface": True,
            },
            "plan": {
                "target_unique": True,
                "child_count": 4,
                "child_order_preserved": True,
                "child_payloads_preserved": True,
                "non_target_payloads_preserved": True,
                "grid_options_change_planned": False,
                "grid_options_count_preserved": True,
                "visual_layout_change_expected": True,
                "custom_sizing_runtime_capability_unresolved": True,
            },
            "authorization": {
                "production_write_authorized": False,
                "explicit_owner_authorization_required": True,
            },
            "rollback": {
                "byte_exact_target_preimage_available": True,
                "rollback_requires_binding_change": False,
            },
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }
        return byte_plan, report
    except SectionsFlatteningApplyPlanError as exc:
        return None, blocked_report(exc.reason)
    except Exception:
        return None, blocked_report("SECTIONS_FLATTENING_APPLY_PREPARE_FAILED")


def build_live_apply_preflight(
    *,
    config_root: Path,
    dashboard_title: str,
    expected_version: str,
    running_version: str,
) -> dict[str, Any]:
    _plan, report = prepare_live_apply(
        config_root=config_root,
        dashboard_title=dashboard_title,
        expected_version=expected_version,
        running_version=running_version,
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a rollback-first byte plan for one bounded native-Sections "
            "flattening. This tool never writes Home Assistant configuration."
        )
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--expected-version", default=EXPECTED_HA_VERSION)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.prepare:
        report = blocked_report("PREPARE_GATE_REQUIRED")
    else:
        try:
            running = running_home_assistant_version()
        except Exception:
            report = blocked_report("HOME_ASSISTANT_VERSION_UNAVAILABLE")
        else:
            report = build_live_apply_preflight(
                config_root=args.config_root,
                dashboard_title=args.dashboard_title,
                expected_version=args.expected_version,
                running_version=running,
            )

    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("decision") == READY_DECISION else 1


if __name__ == "__main__":
    raise SystemExit(main())
