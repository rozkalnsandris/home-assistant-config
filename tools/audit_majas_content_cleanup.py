#!/usr/bin/env python3
"""Audit the active modular Mājas dashboard for private-safe cleanup signals."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from tools.materialize_majas_dashboard_candidate import (
    CandidateError,
    TaggedValue,
    load_candidate_tree,
    structural_counts,
)
from tools.plan_majas_dashboard_activation import (
    ActivationPlanError,
    EXPECTED_CANDIDATE_DIRS,
    EXPECTED_CANDIDATE_FILES,
    resolve_binding_owner,
)
from tools.plan_majas_sections_modernization import EXPECTED_AFTER_STRUCTURE

EXPECTED_HA_VERSION = "2026.8.2"
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"
EXPECTED_TOP_LEVEL_CARD_COUNT = 11
READY_DECISION = "READY_FOR_BOUNDED_CONTENT_CLEANUP_DESIGN"
NO_CANDIDATES_DECISION = "NO_BOUNDED_CONTENT_CLEANUP_CANDIDATES"

ENTITY_LIKE_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
GROUPING_TYPES = {"grid", "horizontal-stack", "vertical-stack"}


class ContentCleanupAuditError(RuntimeError):
    """Sanitized content-cleanup audit failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "binding_values_emitted": False,
        "entity_ids_emitted": False,
        "entity_like_values_emitted": False,
        "view_section_card_titles_emitted": False,
        "card_type_names_emitted": False,
        "custom_card_type_names_emitted": False,
        "actions_or_urls_emitted": False,
        "private_card_payloads_emitted": False,
        "structural_signatures_emitted": False,
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
        "card_or_helper_removed": False,
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


def _active_tree_inventory(active_root: Path) -> tuple[set[str], set[str]]:
    entries = list(active_root.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise ContentCleanupAuditError("ACTIVE_TREE_SYMLINK_PRESENT")

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
        raise ContentCleanupAuditError("ACTIVE_TREE_UNEXPECTED_ENTRY")
    if files != EXPECTED_CANDIDATE_FILES or dirs != EXPECTED_CANDIDATE_DIRS:
        raise ContentCleanupAuditError("ACTIVE_TREE_MISMATCH")
    return files, dirs


def _card_type(card: dict[str, Any]) -> str:
    value = card.get("type")
    return value if isinstance(value, str) else ""


def _top_level_cards(
    payload: dict[str, Any],
) -> list[tuple[tuple[int, int, int], dict[str, Any]]]:
    views = payload.get("views")
    if not isinstance(views, list) or not views:
        raise ContentCleanupAuditError("VIEW_STRUCTURE_UNAVAILABLE")

    cards: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for view_index, view in enumerate(views):
        if not isinstance(view, dict) or view.get("type") != "sections":
            raise ContentCleanupAuditError("POST_PHASE3_LAYOUT_MISMATCH")
        sections = view.get("sections")
        if not isinstance(sections, list):
            raise ContentCleanupAuditError("SECTION_STRUCTURE_UNAVAILABLE")
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                raise ContentCleanupAuditError("SECTION_STRUCTURE_UNAVAILABLE")
            section_cards = section.get("cards")
            if not isinstance(section_cards, list):
                raise ContentCleanupAuditError("CARD_STRUCTURE_UNAVAILABLE")
            for card_index, card in enumerate(section_cards):
                if not isinstance(card, dict):
                    raise ContentCleanupAuditError("CARD_STRUCTURE_UNAVAILABLE")
                cards.append(((view_index, section_index, card_index), card))
    return cards


def _grouping_wrapper_count(cards: list[tuple[tuple[int, int, int], dict[str, Any]]]) -> int:
    return sum(1 for _coord, card in cards if _card_type(card) in GROUPING_TYPES)


def _ordinal(coord: tuple[int, int, int]) -> dict[str, int]:
    view_index, section_index, card_index = coord
    return {
        "view_ordinal": view_index,
        "section_ordinal": section_index,
        "card_ordinal": card_index,
    }


def _exact_duplicate_groups(
    cards: list[tuple[tuple[int, int, int], dict[str, Any]]],
) -> list[list[tuple[int, int, int]]]:
    consumed: set[int] = set()
    groups: list[list[tuple[int, int, int]]] = []

    for index, (coord, card) in enumerate(cards):
        if index in consumed:
            continue
        group = [coord]
        for other_index in range(index + 1, len(cards)):
            if other_index in consumed:
                continue
            other_coord, other_card = cards[other_index]
            if other_card == card:
                consumed.add(other_index)
                group.append(other_coord)
        if len(group) > 1:
            groups.append(group)
    return groups


def _scalar_shape(value: Any) -> tuple[str, ...]:
    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool",)
    if isinstance(value, int):
        return ("int",)
    if isinstance(value, float):
        return ("float",)
    if isinstance(value, str):
        return ("str",)
    return (type(value).__name__,)


def _shape(value: Any) -> Any:
    if isinstance(value, TaggedValue):
        return ("tagged", value.tag, _shape(value.value))
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return (
            "mapping",
            tuple((str(key), _shape(item)) for key, item in items),
        )
    if isinstance(value, list):
        return ("list", tuple(_shape(item) for item in value))
    return _scalar_shape(value)


def _structural_shape_groups(
    cards: list[tuple[tuple[int, int, int], dict[str, Any]]],
) -> list[list[tuple[int, int, int]]]:
    signatures = [_shape(card) for _coord, card in cards]
    consumed: set[int] = set()
    groups: list[list[tuple[int, int, int]]] = []

    for index, signature in enumerate(signatures):
        if index in consumed:
            continue
        group = [cards[index][0]]
        for other_index in range(index + 1, len(signatures)):
            if other_index in consumed:
                continue
            if signatures[other_index] == signature:
                consumed.add(other_index)
                group.append(cards[other_index][0])
        if len(group) > 1:
            groups.append(group)
    return groups


def _iter_strings(value: Any):
    if isinstance(value, TaggedValue):
        yield from _iter_strings(value.value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, str):
        yield value


def _entity_like_reference_counts(payload: dict[str, Any]) -> Counter[str]:
    return Counter(
        value
        for value in _iter_strings(payload)
        if ENTITY_LIKE_RE.fullmatch(value)
    )


def _group_summary(groups: list[list[tuple[int, int, int]]]) -> dict[str, Any]:
    sizes = [len(group) for group in groups]
    return {
        "group_count": len(groups),
        "member_count": sum(sizes),
        "largest_group_size": max(sizes, default=0),
        "groups": [
            [_ordinal(coord) for coord in group]
            for group in groups
        ],
    }


def analyze_content_cleanup(payload: dict[str, Any]) -> dict[str, Any]:
    """Return private-safe cleanup signals from an expanded dashboard payload."""

    counts = structural_counts(payload)
    if counts != EXPECTED_AFTER_STRUCTURE:
        raise ContentCleanupAuditError("POST_PHASE3_STRUCTURE_MISMATCH")

    cards = _top_level_cards(payload)
    if len(cards) != EXPECTED_TOP_LEVEL_CARD_COUNT:
        raise ContentCleanupAuditError("POST_PHASE3_TOP_LEVEL_COUNT_MISMATCH")

    grouping_wrappers = _grouping_wrapper_count(cards)
    if grouping_wrappers != 0:
        raise ContentCleanupAuditError("POST_PHASE3_GROUPING_WRAPPER_PRESENT")

    exact_groups = _exact_duplicate_groups(cards)
    shape_groups = _structural_shape_groups(cards)
    references = _entity_like_reference_counts(payload)

    reasons: list[str] = []
    if exact_groups:
        reasons.append("EXACT_DUPLICATE_CARD_PAYLOAD_CANDIDATES")
    if shape_groups:
        reasons.append("REPEATED_CARD_STRUCTURE_CANDIDATES")

    decision = (
        READY_DECISION
        if reasons
        else NO_CANDIDATES_DECISION
    )

    repeated_reference_counts = [
        count for count in references.values() if count > 1
    ]

    return {
        "decision": decision,
        "reasons": reasons,
        "structure": counts,
        "guard": {
            "all_views_sections": True,
            "top_level_card_count": len(cards),
            "grouping_wrapper_count": grouping_wrappers,
        },
        "candidates": {
            "exact_duplicate_cards": _group_summary(exact_groups),
            "repeated_card_structures": _group_summary(shape_groups),
            "automatic_dedup_safe_claimed": False,
            "automatic_removal_safe_claimed": False,
        },
        "references": {
            "entity_like_occurrence_count": sum(references.values()),
            "unique_entity_like_reference_count": len(references),
            "repeated_entity_like_reference_count": len(repeated_reference_counts),
            "largest_reference_occurrence_count": max(
                references.values(),
                default=0,
            ),
            "unused_entity_or_helper_claimed": False,
        },
    }


def build_live_report(
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

        active_root = active_dashboard.parent.resolve(strict=True)
        files, dirs = _active_tree_inventory(active_root)
        payload = load_candidate_tree(active_root)
        analysis = analyze_content_cleanup(payload)

        return {
            "schema": 1,
            "decision": analysis["decision"],
            "reasons": analysis["reasons"],
            "home_assistant": {
                "expected_version": expected_version,
                "running_version": running_version,
                "version_match": True,
            },
            "binding": {
                "resolved": True,
                "owner_kind": owner_kind,
            },
            "active_tree": {
                "exact": True,
                "regular_files": len(files),
                "directories": len(dirs),
                "symlinks": 0,
                "unexpected_entries": 0,
            },
            "dashboard": {
                "structure": analysis["structure"],
                "guard": analysis["guard"],
                "candidates": analysis["candidates"],
                "references": analysis["references"],
            },
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }
    except (
        ActivationPlanError,
        CandidateError,
        ContentCleanupAuditError,
    ) as exc:
        return blocked_report(exc.reason)
    except Exception:
        return blocked_report("CONTENT_CLEANUP_AUDIT_FAILED")


def running_home_assistant_version() -> str:
    try:
        from homeassistant.const import __version__
    except (ImportError, AttributeError) as exc:
        raise ContentCleanupAuditError("HOME_ASSISTANT_VERSION_UNAVAILABLE") from exc
    return str(__version__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the active modular Mājas dashboard for private-safe content "
            "cleanup signals. The tool is strictly read-only."
        )
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--expected-version", default=EXPECTED_HA_VERSION)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.audit:
        report = blocked_report("AUDIT_GATE_REQUIRED")
    else:
        try:
            running = running_home_assistant_version()
        except ContentCleanupAuditError as exc:
            report = blocked_report(exc.reason)
        else:
            report = build_live_report(
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
