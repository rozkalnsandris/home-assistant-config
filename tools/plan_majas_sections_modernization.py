#!/usr/bin/env python3
"""Plan one bounded native-Sections modernization without writing live config."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from tools.audit_majas_sections_modernization import analyze_sections_layout
from tools.materialize_majas_dashboard_candidate import (
    load_candidate_tree,
    structural_counts,
)
from tools.plan_majas_dashboard_activation import resolve_binding_owner

EXPECTED_HA_VERSION = "2026.8.2"
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"

EXPECTED_BEFORE_STRUCTURE = {
    "view_count": 1,
    "section_count": 3,
    "card_count": 12,
    "custom_card_count": 11,
    "distinct_custom_card_type_count": 1,
}
EXPECTED_AFTER_STRUCTURE = {
    "view_count": 1,
    "section_count": 3,
    "card_count": 11,
    "custom_card_count": 11,
    "distinct_custom_card_type_count": 1,
}
ALLOWED_GRID_WRAPPER_KEYS = {"type", "title", "square", "columns", "cards"}


class SectionsModernizationPlanError(RuntimeError):
    """Sanitized planner failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_custom(card: Any) -> bool:
    if not isinstance(card, dict):
        return False
    card_type = card.get("type")
    return isinstance(card_type, str) and card_type.startswith("custom:")


def _count_top_level_cards(payload: dict[str, Any]) -> int:
    total = 0
    views = payload.get("views")
    if not isinstance(views, list):
        return 0
    for view in views:
        if not isinstance(view, dict):
            continue
        sections = view.get("sections")
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            cards = section.get("cards")
            if isinstance(cards, list):
                total += len(cards)
    return total


def _count_top_level_grouping_wrappers(payload: dict[str, Any]) -> int:
    total = 0
    views = payload.get("views")
    if not isinstance(views, list):
        return 0
    for view in views:
        if not isinstance(view, dict):
            continue
        sections = view.get("sections")
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            cards = section.get("cards")
            if not isinstance(cards, list):
                continue
            for card in cards:
                if not isinstance(card, dict):
                    continue
                if card.get("type") in {"grid", "horizontal-stack", "vertical-stack"}:
                    total += 1
    return total


def _count_explicit_grid_options(payload: dict[str, Any]) -> int:
    total = 0

    def visit(card: Any) -> None:
        nonlocal total
        if not isinstance(card, dict):
            return
        if "grid_options" in card:
            total += 1
        children = card.get("cards")
        if isinstance(children, list):
            for child in children:
                visit(child)
        child = card.get("card")
        if isinstance(child, dict):
            visit(child)

    views = payload.get("views")
    if not isinstance(views, list):
        return 0
    for view in views:
        if not isinstance(view, dict):
            continue
        sections = view.get("sections")
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            cards = section.get("cards")
            if isinstance(cards, list):
                for card in cards:
                    visit(card)
    return total


def _qualifying_grid_targets(
    payload: dict[str, Any],
) -> list[tuple[int, int, int, dict[str, Any]]]:
    matches: list[tuple[int, int, int, dict[str, Any]]] = []
    views = payload.get("views")
    if not isinstance(views, list):
        return matches

    for view_index, view in enumerate(views):
        if not isinstance(view, dict):
            continue
        sections = view.get("sections")
        if not isinstance(sections, list):
            continue
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            cards = section.get("cards")
            if not isinstance(cards, list):
                continue
            for card_index, card in enumerate(cards):
                if isinstance(card, dict) and card.get("type") == "grid":
                    matches.append((view_index, section_index, card_index, card))
    return matches


def _non_target_preserved(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    view_index: int,
    section_index: int,
    card_index: int,
    child_count: int,
) -> bool:
    before_copy = copy.deepcopy(before)
    after_copy = copy.deepcopy(after)

    before_cards = before_copy["views"][view_index]["sections"][section_index]["cards"]
    after_cards = after_copy["views"][view_index]["sections"][section_index]["cards"]

    del before_cards[card_index]
    del after_cards[card_index : card_index + child_count]

    return before_copy == after_copy


def _target_children_preserved(
    wrapper: dict[str, Any],
    proposal: dict[str, Any],
    *,
    view_index: int,
    section_index: int,
    card_index: int,
) -> bool:
    children = wrapper.get("cards")
    if not isinstance(children, list):
        return False

    proposal_cards = proposal["views"][view_index]["sections"][section_index]["cards"]
    proposed_children = proposal_cards[card_index : card_index + len(children)]
    return proposed_children == children


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
    }


def mutation_report() -> dict[str, bool]:
    return {
        "owner_file_modified": False,
        "dashboard_modified": False,
        "candidate_tree_modified": False,
        "old_source_modified": False,
        "storage_write": False,
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


def analyze_flattening_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized in-memory plan for one bounded grid-wrapper flattening."""

    before_structure = structural_counts(payload)
    if before_structure != EXPECTED_BEFORE_STRUCTURE:
        raise SectionsModernizationPlanError("BASELINE_STRUCTURE_MISMATCH")

    audit = analyze_sections_layout(payload)
    if audit.get("decision") != "READY_FOR_BOUNDED_SECTIONS_MODERNIZATION_DESIGN":
        raise SectionsModernizationPlanError("MODERNIZATION_SIGNAL_MISMATCH")

    layout = audit.get("layout")
    if not isinstance(layout, dict) or layout.get("all_views_sections") is not True:
        raise SectionsModernizationPlanError("NATIVE_SECTIONS_REQUIRED")

    top_level = layout.get("top_level")
    grouping = layout.get("grouping_wrappers")
    custom = layout.get("custom_cards")
    if (
        not isinstance(top_level, dict)
        or not isinstance(grouping, dict)
        or not isinstance(custom, dict)
    ):
        raise SectionsModernizationPlanError("AUDIT_SHAPE_UNAVAILABLE")

    if (
        top_level.get("card_count") != 8
        or top_level.get("grid_card_count") != 1
        or top_level.get("stack_card_count") != 0
        or grouping.get("nested_card_count") != 4
        or grouping.get("nested_custom_card_count") != 4
        or grouping.get("nested_grid_card_count") != 0
        or grouping.get("nested_stack_card_count") != 0
    ):
        raise SectionsModernizationPlanError("GROUPING_BASELINE_MISMATCH")

    if custom.get("default_sizing_runtime_capability_unknown") is not True:
        raise SectionsModernizationPlanError("CUSTOM_SIZING_EVIDENCE_DRIFT")

    targets = _qualifying_grid_targets(payload)
    if len(targets) != 1:
        raise SectionsModernizationPlanError("GRID_WRAPPER_NOT_UNIQUE")

    view_index, section_index, card_index, wrapper = targets[0]

    unknown_keys = set(wrapper) - ALLOWED_GRID_WRAPPER_KEYS
    if unknown_keys:
        raise SectionsModernizationPlanError("GRID_WRAPPER_KEYS_UNSUPPORTED")

    title = wrapper.get("title")
    if title not in (None, ""):
        return {
            "schema": 1,
            "decision": "NEEDS_PRIVATE_REVIEW",
            "reasons": ["GRID_WRAPPER_TITLE_PRESENT"],
            "plan": {
                "target_unique": True,
                "wrapper_documented_keys_only": True,
                "wrapper_title_present": True,
                "child_count": 4,
                "grid_options_change_planned": False,
                "visual_layout_change_expected": True,
            },
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }

    children = wrapper.get("cards")
    if not isinstance(children, list) or len(children) != 4:
        raise SectionsModernizationPlanError("GRID_WRAPPER_CHILD_COUNT_MISMATCH")
    if any(not isinstance(child, dict) for child in children):
        raise SectionsModernizationPlanError("GRID_WRAPPER_CHILD_MAPPING_REQUIRED")
    if any(not _is_custom(child) for child in children):
        raise SectionsModernizationPlanError("GRID_WRAPPER_CHILD_TYPE_MISMATCH")

    before_top_level = _count_top_level_cards(payload)
    before_grouping = _count_top_level_grouping_wrappers(payload)
    before_grid_options = _count_explicit_grid_options(payload)

    proposal = copy.deepcopy(payload)
    proposal_cards = proposal["views"][view_index]["sections"][section_index]["cards"]
    proposal_cards[card_index : card_index + 1] = copy.deepcopy(children)

    after_structure = structural_counts(proposal)
    if after_structure != EXPECTED_AFTER_STRUCTURE:
        raise SectionsModernizationPlanError("PROPOSED_STRUCTURE_MISMATCH")

    after_top_level = _count_top_level_cards(proposal)
    after_grouping = _count_top_level_grouping_wrappers(proposal)
    after_grid_options = _count_explicit_grid_options(proposal)

    child_payloads_preserved = _target_children_preserved(
        wrapper,
        proposal,
        view_index=view_index,
        section_index=section_index,
        card_index=card_index,
    )
    non_target_preserved = _non_target_preserved(
        payload,
        proposal,
        view_index=view_index,
        section_index=section_index,
        card_index=card_index,
        child_count=len(children),
    )

    if not child_payloads_preserved:
        raise SectionsModernizationPlanError("TARGET_CHILD_PAYLOAD_DRIFT")
    if not non_target_preserved:
        raise SectionsModernizationPlanError("NON_TARGET_PAYLOAD_DRIFT")
    if before_top_level != 8 or after_top_level != 11:
        raise SectionsModernizationPlanError("TOP_LEVEL_CARD_COUNT_MISMATCH")
    if before_grouping != 1 or after_grouping != 0:
        raise SectionsModernizationPlanError("GROUPING_WRAPPER_COUNT_MISMATCH")
    if before_grid_options != after_grid_options:
        raise SectionsModernizationPlanError("GRID_OPTIONS_DRIFT")

    return {
        "schema": 1,
        "decision": "READY_FOR_PRIVATE_SECTIONS_FLATTENING_DRY_RUN",
        "reasons": [],
        "structure": {
            "before": before_structure,
            "proposed": after_structure,
            "top_level_card_count_before": before_top_level,
            "top_level_card_count_proposed": after_top_level,
            "grouping_wrapper_count_before": before_grouping,
            "grouping_wrapper_count_proposed": after_grouping,
        },
        "plan": {
            "target_unique": True,
            "target_view_ordinal": view_index,
            "target_section_ordinal": section_index,
            "target_card_ordinal": card_index,
            "wrapper_documented_keys_only": True,
            "wrapper_title_present": False,
            "child_count": len(children),
            "all_children_custom": True,
            "child_order_preserved": True,
            "child_payloads_preserved": True,
            "non_target_payloads_preserved": True,
            "grid_options_change_planned": False,
            "grid_options_count_preserved": True,
            "visual_layout_change_expected": True,
            "card_config_payload_preserved": True,
            "custom_sizing_runtime_capability_unresolved": True,
        },
        "privacy": privacy_report(),
        "mutation": mutation_report(),
    }


def build_live_plan(
    *,
    config_root: Path,
    dashboard_title: str,
    expected_version: str,
    running_version: str,
) -> dict[str, Any]:
    if running_version != expected_version:
        return blocked_report("HOME_ASSISTANT_VERSION_MISMATCH")

    try:
        root = config_root.resolve(strict=True)
        (
            _owner_path,
            owner_kind,
            _owner_payload,
            _dashboard_key,
            _definition,
            active_dashboard,
        ) = resolve_binding_owner(root, dashboard_title)

        payload = load_candidate_tree(active_dashboard.parent)
        plan = analyze_flattening_plan(payload)

        return {
            **plan,
            "home_assistant": {
                "expected_version": expected_version,
                "running_version": running_version,
                "version_match": True,
            },
            "binding": {
                "resolved": True,
                "owner_kind": owner_kind,
            },
        }
    except SectionsModernizationPlanError as exc:
        return blocked_report(exc.reason)
    except Exception:
        return blocked_report("SECTIONS_FLATTENING_PLAN_FAILED")


def running_home_assistant_version() -> str:
    try:
        from homeassistant.const import __version__
    except (ImportError, AttributeError) as exc:
        raise SectionsModernizationPlanError("HOME_ASSISTANT_VERSION_UNAVAILABLE") from exc
    return str(__version__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan one bounded native-Sections grid-wrapper flattening. "
            "The planner is read-only and never writes Home Assistant configuration."
        )
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--expected-version", default=EXPECTED_HA_VERSION)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.plan:
        report = blocked_report("PLAN_GATE_REQUIRED")
    else:
        try:
            running = running_home_assistant_version()
        except SectionsModernizationPlanError as exc:
            report = blocked_report(exc.reason)
        else:
            report = build_live_plan(
                config_root=args.config_root,
                dashboard_title=args.dashboard_title,
                expected_version=args.expected_version,
                running_version=running,
            )

    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))

    return 1 if report.get("decision") == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
