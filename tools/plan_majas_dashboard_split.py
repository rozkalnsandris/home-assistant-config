#!/usr/bin/env python3
"""Plan and verify the Phase 2A Mājas YAML split without writing private data."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import yaml

EXPECTED_VIEW_COUNT = 1
EXPECTED_SECTION_COUNT = 3
EXPECTED_CARD_COUNT = 12
EXPECTED_CUSTOM_CARD_COUNT = 11
EXPECTED_DISTINCT_CUSTOM_CARD_TYPE_COUNT = 1


class DashboardLoader(yaml.SafeLoader):
    """YAML loader that preserves unknown Home Assistant tags as inert values."""


def _unknown_tag(
    loader: DashboardLoader, tag_suffix: str, node: yaml.Node
) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = None
    return {"__ha_tag__": f"!{tag_suffix}", "value": value}


DashboardLoader.add_multi_constructor("!", _unknown_tag)


def load_dashboard(path: Path) -> dict[str, Any]:
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=DashboardLoader)
    if not isinstance(payload, dict):
        raise ValueError("dashboard root must be a mapping")
    return payload


def _card_metrics(card: Any) -> dict[str, Any]:
    metrics = {
        "cards": 0,
        "custom_cards": 0,
        "custom_types": set(),
    }
    if not isinstance(card, dict):
        return metrics

    metrics["cards"] = 1
    card_type = card.get("type")
    if isinstance(card_type, str) and card_type.startswith("custom:"):
        metrics["custom_cards"] = 1
        metrics["custom_types"].add(card_type)

    children: list[Any] = []
    if isinstance(card.get("cards"), list):
        children.extend(card["cards"])
    if isinstance(card.get("card"), dict):
        children.append(card["card"])

    for child in children:
        child_metrics = _card_metrics(child)
        metrics["cards"] += child_metrics["cards"]
        metrics["custom_cards"] += child_metrics["custom_cards"]
        metrics["custom_types"].update(child_metrics["custom_types"])
    return metrics


def structural_counts(payload: dict[str, Any]) -> dict[str, int]:
    views = payload.get("views")
    if not isinstance(views, list):
        return {
            "view_count": 0,
            "section_count": 0,
            "card_count": 0,
            "custom_card_count": 0,
            "distinct_custom_card_type_count": 0,
        }

    section_count = 0
    card_count = 0
    custom_card_count = 0
    custom_types: set[str] = set()

    for view in views:
        if not isinstance(view, dict):
            continue

        cards = view.get("cards")
        if isinstance(cards, list):
            for card in cards:
                metrics = _card_metrics(card)
                card_count += metrics["cards"]
                custom_card_count += metrics["custom_cards"]
                custom_types.update(metrics["custom_types"])

        sections = view.get("sections")
        if isinstance(sections, list):
            section_count += len(sections)
            for section in sections:
                if not isinstance(section, dict):
                    continue
                section_cards = section.get("cards")
                if not isinstance(section_cards, list):
                    continue
                for card in section_cards:
                    metrics = _card_metrics(card)
                    card_count += metrics["cards"]
                    custom_card_count += metrics["custom_cards"]
                    custom_types.update(metrics["custom_types"])

    return {
        "view_count": len(views),
        "section_count": section_count,
        "card_count": card_count,
        "custom_card_count": custom_card_count,
        "distinct_custom_card_type_count": len(custom_types),
    }


def _expected_shape(counts: dict[str, int]) -> bool:
    return counts == {
        "view_count": EXPECTED_VIEW_COUNT,
        "section_count": EXPECTED_SECTION_COUNT,
        "card_count": EXPECTED_CARD_COUNT,
        "custom_card_count": EXPECTED_CUSTOM_CARD_COUNT,
        "distinct_custom_card_type_count": EXPECTED_DISTINCT_CUSTOM_CARD_TYPE_COUNT,
    }


def split_in_memory(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a private in-memory split model; never writes or renders YAML."""
    counts = structural_counts(payload)
    if not _expected_shape(counts):
        raise ValueError("dashboard structure does not match the reviewed Phase 2A baseline")

    views = payload.get("views")
    assert isinstance(views, list) and len(views) == 1
    view = views[0]
    if not isinstance(view, dict):
        raise ValueError("view_00 must be a mapping")

    sections = view.get("sections")
    if not isinstance(sections, list) or len(sections) != EXPECTED_SECTION_COUNT:
        raise ValueError("view_00 must contain exactly three sections")
    if any(not isinstance(section, dict) for section in sections):
        raise ValueError("each section must be a mapping")

    dashboard_root = copy.deepcopy(payload)
    dashboard_root.pop("views")

    view_root = copy.deepcopy(view)
    view_root.pop("sections")

    return {
        "dashboard_root": dashboard_root,
        "view_root": view_root,
        "sections": copy.deepcopy(sections),
    }


def reassemble(split_model: dict[str, Any]) -> dict[str, Any]:
    dashboard_root = copy.deepcopy(split_model["dashboard_root"])
    view_root = copy.deepcopy(split_model["view_root"])
    sections = copy.deepcopy(split_model["sections"])
    view_root["sections"] = sections
    dashboard_root["views"] = [view_root]
    return dashboard_root


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    before = structural_counts(payload)
    reasons: list[str] = []

    if not _expected_shape(before):
        reasons.append("BASELINE_STRUCTURE_MISMATCH")
        return {
            "schema": 1,
            "decision": "BLOCKED",
            "reasons": reasons,
            "structure": {"before": before},
            "equivalence": {
                "assembled_candidate_parses": False,
                "dashboard_level_preserved": False,
                "view_non_section_fields_preserved": False,
                "section_payloads_and_order_preserved": False,
                "whole_structure_equivalent": False,
            },
            "privacy": {"raw_private_values_emitted": False},
            "mutation": {
                "filesystem_write": False,
                "live_dashboard_binding_changed": False,
                "reload_or_restart": False,
            },
        }

    try:
        split_model = split_in_memory(payload)
        assembled = reassemble(split_model)
    except (KeyError, TypeError, ValueError):
        reasons.append("SPLIT_MODEL_INVALID")
        return {
            "schema": 1,
            "decision": "BLOCKED",
            "reasons": reasons,
            "structure": {"before": before},
            "equivalence": {
                "assembled_candidate_parses": False,
                "dashboard_level_preserved": False,
                "view_non_section_fields_preserved": False,
                "section_payloads_and_order_preserved": False,
                "whole_structure_equivalent": False,
            },
            "privacy": {"raw_private_values_emitted": False},
            "mutation": {
                "filesystem_write": False,
                "live_dashboard_binding_changed": False,
                "reload_or_restart": False,
            },
        }

    after = structural_counts(assembled)
    original_views = payload["views"]
    assembled_views = assembled["views"]
    assert isinstance(original_views, list) and isinstance(assembled_views, list)
    original_view = original_views[0]
    assembled_view = assembled_views[0]
    assert isinstance(original_view, dict) and isinstance(assembled_view, dict)

    original_root = copy.deepcopy(payload)
    original_root.pop("views")
    assembled_root = copy.deepcopy(assembled)
    assembled_root.pop("views")

    original_view_fields = copy.deepcopy(original_view)
    original_sections = original_view_fields.pop("sections")
    assembled_view_fields = copy.deepcopy(assembled_view)
    assembled_sections = assembled_view_fields.pop("sections")

    equivalence = {
        "assembled_candidate_parses": True,
        "dashboard_level_preserved": original_root == assembled_root,
        "view_non_section_fields_preserved": original_view_fields == assembled_view_fields,
        "section_payloads_and_order_preserved": original_sections == assembled_sections,
        "whole_structure_equivalent": payload == assembled,
    }

    if before != after:
        reasons.append("STRUCTURAL_COUNTS_CHANGED")
    if not all(equivalence.values()):
        reasons.append("SEMANTIC_EQUIVALENCE_FAILED")

    return {
        "schema": 1,
        "decision": "READY_FOR_PRIVATE_CANDIDATE_GATE" if not reasons else "BLOCKED",
        "reasons": reasons,
        "structure": {
            "before": before,
            "after": after,
            "ordered_section_count": len(split_model["sections"]),
        },
        "equivalence": equivalence,
        "privacy": {"raw_private_values_emitted": False},
        "mutation": {
            "filesystem_write": False,
            "live_dashboard_binding_changed": False,
            "reload_or_restart": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a private Mājas dashboard, verify the reviewed 1-view/3-section "
            "split entirely in memory, and emit only sanitized equivalence evidence."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(load_dashboard(args.input))
    except (OSError, UnicodeError, yaml.YAMLError, ValueError):
        report = {
            "schema": 1,
            "decision": "BLOCKED",
            "reasons": ["INPUT_UNREADABLE_OR_UNPARSABLE"],
            "privacy": {"raw_private_values_emitted": False},
            "mutation": {
                "filesystem_write": False,
                "live_dashboard_binding_changed": False,
                "reload_or_restart": False,
            },
        }

    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["decision"] != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
