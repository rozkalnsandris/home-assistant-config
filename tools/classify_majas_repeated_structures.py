#!/usr/bin/env python3
"""Classify repeated Mājas card structures without exposing private payload data."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import json
from pathlib import Path
from typing import Any

from tools.audit_majas_content_cleanup import (
    DEFAULT_CONFIG_ROOT,
    DEFAULT_DASHBOARD_TITLE,
    EXPECTED_HA_VERSION,
    ENTITY_LIKE_RE,
    ContentCleanupAuditError,
    _structural_shape_groups,
    _top_level_cards,
    build_live_report as build_phase4a_report,
    mutation_report,
    privacy_report,
)
from tools.materialize_majas_dashboard_candidate import (
    CandidateError,
    TaggedValue,
    load_candidate_tree,
)
from tools.plan_majas_dashboard_activation import (
    ActivationPlanError,
    resolve_binding_owner,
)

EXPECTED_REPEATED_GROUP_COUNT = 3
EXPECTED_REPEATED_MEMBER_COUNT = 7
EXPECTED_EXACT_DUPLICATE_GROUP_COUNT = 0

NO_DEDUP_DECISION = "NO_BOUNDED_LAYOUT_DEDUP_CANDIDATE"
READY_REFACTOR_DECISION = "READY_FOR_BOUNDED_LAYOUT_REFACTOR_DESIGN"

PARAMETERIZED_PATTERN = "PARAMETERIZED_REPEATED_UI_PATTERN"
BEHAVIORALLY_DISTINCT_PATTERN = "BEHAVIORALLY_DISTINCT_REPEATED_PATTERN"

BEHAVIOR_KEYS = frozenset(
    {
        "tap_action",
        "hold_action",
        "double_tap_action",
        "action",
        "service",
        "perform_action",
        "target",
        "navigation_path",
        "url_path",
        "url",
        "confirmation",
        "service_data",
        "data",
    }
)


class RepeatedStructureClassificationError(RuntimeError):
    """Sanitized repeated-structure classification failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": [reason],
        "privacy": privacy_report(),
        "mutation": mutation_report(),
    }


def _ordinal(coord: tuple[int, int, int]) -> dict[str, int]:
    view_index, section_index, card_index = coord
    return {
        "view_ordinal": view_index,
        "section_ordinal": section_index,
        "card_ordinal": card_index,
    }


def _canonical(value: Any) -> Any:
    if isinstance(value, TaggedValue):
        return ("tagged", value.tag, _canonical(value.value))
    if isinstance(value, dict):
        return (
            "mapping",
            tuple(
                (str(key), _canonical(item))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ),
        )
    if isinstance(value, list):
        return ("list", tuple(_canonical(item) for item in value))
    if value is None or isinstance(value, (bool, int, float, str)):
        return (type(value).__name__, value)
    return (type(value).__name__, repr(value))


def _behavior_projection(value: Any, path: tuple[str, ...] = ()) -> tuple[Any, ...]:
    rows: list[Any] = []

    if isinstance(value, TaggedValue):
        rows.extend(_behavior_projection(value.value, path))
        return tuple(rows)

    if isinstance(value, dict):
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            key_text = str(key)
            child_path = (*path, key_text)
            if key_text in BEHAVIOR_KEYS:
                rows.append((child_path, _canonical(item)))
            rows.extend(_behavior_projection(item, child_path))
        return tuple(rows)

    if isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(_behavior_projection(item, (*path, f"[{index}]")))
        return tuple(rows)

    return tuple(rows)


def _iter_scalar_leaves(
    value: Any,
    path: tuple[str, ...] = (),
) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(value, TaggedValue):
        yield from _iter_scalar_leaves(value.value, (*path, "<tagged>"))
        return

    if isinstance(value, dict):
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            yield from _iter_scalar_leaves(item, (*path, str(key)))
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_scalar_leaves(item, (*path, f"[{index}]"))
        return

    yield path, _canonical(value)


def _scalar_map(value: Any) -> dict[tuple[str, ...], Any]:
    return dict(_iter_scalar_leaves(value))


def _scalar_difference_path_count(cards: list[dict[str, Any]]) -> int:
    maps = [_scalar_map(card) for card in cards]
    if not maps:
        return 0

    paths = set().union(*(mapping.keys() for mapping in maps))
    return sum(
        1
        for path in paths
        if len({mapping.get(path) for mapping in maps}) > 1
    )


def _entity_like_references(value: Any) -> frozenset[str]:
    values: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, TaggedValue):
            walk(item.value)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str) and ENTITY_LIKE_RE.fullmatch(item):
            values.add(item)

    walk(value)
    return frozenset(values)


def _group_cards(
    cards: list[tuple[tuple[int, int, int], dict[str, Any]]],
    coords: list[tuple[int, int, int]],
) -> list[dict[str, Any]]:
    by_coord = {coord: card for coord, card in cards}
    try:
        return [by_coord[coord] for coord in coords]
    except KeyError as exc:
        raise RepeatedStructureClassificationError(
            "REPEATED_GROUP_MEMBER_UNAVAILABLE"
        ) from exc


def classify_repeated_structures(payload: dict[str, Any]) -> dict[str, Any]:
    """Classify repeated structural groups using private values only in memory."""

    cards = _top_level_cards(payload)
    groups = _structural_shape_groups(cards)

    group_count = len(groups)
    member_count = sum(len(group) for group in groups)
    if group_count != EXPECTED_REPEATED_GROUP_COUNT:
        raise RepeatedStructureClassificationError("REPEATED_GROUP_COUNT_DRIFT")
    if member_count != EXPECTED_REPEATED_MEMBER_COUNT:
        raise RepeatedStructureClassificationError("REPEATED_MEMBER_COUNT_DRIFT")

    reports: list[dict[str, Any]] = []
    behaviorally_distinct_count = 0
    parameterized_count = 0

    for group_ordinal, coords in enumerate(groups):
        members = _group_cards(cards, coords)
        canonical_members = [_canonical(member) for member in members]
        exact_duplicate = len(set(canonical_members)) == 1
        if exact_duplicate:
            raise RepeatedStructureClassificationError(
                "EXACT_DUPLICATE_DRIFT_IN_REPEATED_GROUP"
            )

        behavior = [_behavior_projection(member) for member in members]
        behavior_present = any(bool(item) for item in behavior)
        behavior_identical = len(set(behavior)) == 1
        behavior_differs = not behavior_identical

        entity_sets = [_entity_like_references(member) for member in members]
        entity_like_reference_sets_differ = len(set(entity_sets)) > 1

        scalar_difference_count = _scalar_difference_path_count(members)
        scalar_values_differ = scalar_difference_count > 0

        if behavior_differs:
            classification = BEHAVIORALLY_DISTINCT_PATTERN
            behaviorally_distinct_count += 1
        else:
            classification = PARAMETERIZED_PATTERN
            parameterized_count += 1

        reports.append(
            {
                "group_ordinal": group_ordinal,
                "member_count": len(coords),
                "members": [_ordinal(coord) for coord in coords],
                "exact_duplicate": False,
                "scalar_leaf_values_differ": scalar_values_differ,
                "scalar_difference_path_count": scalar_difference_count,
                "entity_like_reference_sets_differ": entity_like_reference_sets_differ,
                "behavioral_surface_present": behavior_present,
                "behavioral_surface_identical": behavior_identical,
                "behavioral_surface_differs": behavior_differs,
                "differences_confined_to_non_behavioral_scalars": behavior_identical,
                "classification": classification,
                "bounded_refactor_candidate": False,
                "semantics_preserving_reuse_mechanism_proven": False,
            }
        )

    return {
        "decision": NO_DEDUP_DECISION,
        "reasons": ["NO_SEMANTICS_PRESERVING_REUSE_MECHANISM_PROVEN"],
        "summary": {
            "repeated_group_count": group_count,
            "repeated_member_count": member_count,
            "parameterized_group_count": parameterized_count,
            "behaviorally_distinct_group_count": behaviorally_distinct_count,
            "bounded_refactor_candidate_count": 0,
            "semantics_preserving_reuse_mechanism_proven": False,
        },
        "groups": reports,
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
        phase4a = build_phase4a_report(
            config_root=config_root,
            dashboard_title=dashboard_title,
            expected_version=expected_version,
            running_version=running_version,
        )
        if phase4a.get("decision") == "BLOCKED":
            reasons = phase4a.get("reasons") or ["PHASE4A_BASELINE_BLOCKED"]
            return blocked_report(str(reasons[0]))

        dashboard = phase4a.get("dashboard", {})
        candidates = dashboard.get("candidates", {})
        exact = candidates.get("exact_duplicate_cards", {})
        repeated = candidates.get("repeated_card_structures", {})

        if exact.get("group_count") != EXPECTED_EXACT_DUPLICATE_GROUP_COUNT:
            return blocked_report("EXACT_DUPLICATE_GROUP_COUNT_DRIFT")
        if repeated.get("group_count") != EXPECTED_REPEATED_GROUP_COUNT:
            return blocked_report("REPEATED_GROUP_COUNT_DRIFT")
        if repeated.get("member_count") != EXPECTED_REPEATED_MEMBER_COUNT:
            return blocked_report("REPEATED_MEMBER_COUNT_DRIFT")

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
        payload = load_candidate_tree(active_root)
        classification = classify_repeated_structures(payload)

        return {
            "schema": 1,
            "decision": classification["decision"],
            "reasons": classification["reasons"],
            "home_assistant": {
                "expected_version": expected_version,
                "running_version": running_version,
                "version_match": True,
            },
            "binding": {
                "resolved": True,
                "owner_kind": owner_kind,
            },
            "active_tree": phase4a["active_tree"],
            "dashboard": {
                "structure": dashboard["structure"],
                "guard": dashboard["guard"],
                "phase4a_candidate_summary": {
                    "exact_duplicate_group_count": exact["group_count"],
                    "repeated_group_count": repeated["group_count"],
                    "repeated_member_count": repeated["member_count"],
                },
                "classification": classification,
            },
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }
    except (
        ActivationPlanError,
        CandidateError,
        ContentCleanupAuditError,
        RepeatedStructureClassificationError,
    ) as exc:
        reason = getattr(exc, "reason", "REPEATED_STRUCTURE_CLASSIFICATION_FAILED")
        return blocked_report(str(reason))
    except Exception:
        return blocked_report("REPEATED_STRUCTURE_CLASSIFICATION_FAILED")


def running_home_assistant_version() -> str:
    try:
        from homeassistant.const import __version__
    except (ImportError, AttributeError) as exc:
        raise RepeatedStructureClassificationError(
            "HOME_ASSISTANT_VERSION_UNAVAILABLE"
        ) from exc
    return str(__version__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Privately classify repeated Mājas card structures. The tool is strictly "
            "read-only and never claims a refactor without a proven reuse mechanism."
        )
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--expected-version", default=EXPECTED_HA_VERSION)
    parser.add_argument("--classify", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.classify:
        report = blocked_report("CLASSIFICATION_GATE_REQUIRED")
    else:
        try:
            running = running_home_assistant_version()
        except RepeatedStructureClassificationError as exc:
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
