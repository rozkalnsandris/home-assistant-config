#!/usr/bin/env python3
"""Audit a modular Mājas dashboard for private-safe Sections modernization signals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.materialize_majas_dashboard_candidate import (
    expected_shape,
    load_candidate_tree,
    structural_counts,
)
from tools.plan_majas_dashboard_activation import resolve_binding_owner

EXPECTED_HA_VERSION = "2026.8.2"
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"

GROUPING_CARD_TYPES = {"grid", "horizontal-stack", "vertical-stack"}


class ModernizationAuditError(RuntimeError):
    """Sanitized modernization-audit failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_custom(card: Any) -> bool:
    if not isinstance(card, dict):
        return False
    card_type = card.get("type")
    return isinstance(card_type, str) and card_type.startswith("custom:")


def _card_type(card: Any) -> str:
    if not isinstance(card, dict):
        return ""
    card_type = card.get("type")
    return card_type if isinstance(card_type, str) else ""


def _grid_width_class(card: dict[str, Any]) -> str:
    if "grid_options" not in card:
        return "default"

    options = card.get("grid_options")
    if not isinstance(options, dict):
        return "invalid"

    columns = options.get("columns")
    if columns == "full":
        return "full"

    if isinstance(columns, int) and not isinstance(columns, bool) and columns > 0:
        return "bounded"

    if columns is None:
        return "unspecified"

    return "invalid"


def _grouping_descendants(card: Any) -> list[dict[str, Any]]:
    if not isinstance(card, dict):
        return []

    card_type = _card_type(card)
    if card_type not in GROUPING_CARD_TYPES:
        return []

    children = card.get("cards")
    if not isinstance(children, list):
        return []

    descendants: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        descendants.append(child)
        descendants.extend(_grouping_descendants(child))
    return descendants


def _max_columns_class(value: Any) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return "invalid"
    if value == 1:
        return "one"
    if value == 2:
        return "two"
    if value == 3:
        return "three"
    return "four-plus"


def _dense_placement_class(view: dict[str, Any]) -> str:
    if "dense_section_placement" not in view:
        return "unset"
    value = view.get("dense_section_placement")
    if value is True:
        return "enabled"
    if value is False:
        return "disabled"
    return "invalid"


def _new_totals() -> dict[str, int]:
    return {
        "top_level_card_count": 0,
        "top_level_custom_card_count": 0,
        "top_level_grid_card_count": 0,
        "top_level_stack_card_count": 0,
        "top_level_explicit_grid_options_count": 0,
        "top_level_default_grid_options_count": 0,
        "top_level_full_width_count": 0,
        "top_level_bounded_width_count": 0,
        "top_level_unspecified_width_count": 0,
        "top_level_invalid_grid_options_count": 0,
        "custom_explicit_grid_options_count": 0,
        "custom_default_grid_options_count": 0,
        "grouping_nested_card_count": 0,
        "grouping_nested_custom_card_count": 0,
        "grouping_nested_grid_card_count": 0,
        "grouping_nested_stack_card_count": 0,
    }


def _add_card_metrics(totals: dict[str, int], card: dict[str, Any]) -> None:
    totals["top_level_card_count"] += 1

    card_type = _card_type(card)
    custom = _is_custom(card)

    if custom:
        totals["top_level_custom_card_count"] += 1

    if card_type == "grid":
        totals["top_level_grid_card_count"] += 1
    elif card_type in {"horizontal-stack", "vertical-stack"}:
        totals["top_level_stack_card_count"] += 1

    width_class = _grid_width_class(card)
    if width_class == "default":
        totals["top_level_default_grid_options_count"] += 1
    else:
        totals["top_level_explicit_grid_options_count"] += 1

    if width_class == "full":
        totals["top_level_full_width_count"] += 1
    elif width_class == "bounded":
        totals["top_level_bounded_width_count"] += 1
    elif width_class == "unspecified":
        totals["top_level_unspecified_width_count"] += 1
    elif width_class == "invalid":
        totals["top_level_invalid_grid_options_count"] += 1

    if custom:
        if width_class == "default":
            totals["custom_default_grid_options_count"] += 1
        else:
            totals["custom_explicit_grid_options_count"] += 1

    for nested in _grouping_descendants(card):
        totals["grouping_nested_card_count"] += 1

        nested_type = _card_type(nested)
        if _is_custom(nested):
            totals["grouping_nested_custom_card_count"] += 1
        if nested_type == "grid":
            totals["grouping_nested_grid_card_count"] += 1
        elif nested_type in {"horizontal-stack", "vertical-stack"}:
            totals["grouping_nested_stack_card_count"] += 1


def _merge_totals(target: dict[str, int], source: dict[str, int]) -> None:
    for key in target:
        target[key] += source[key]


def analyze_sections_layout(payload: dict[str, Any]) -> dict[str, Any]:
    """Return sanitized layout-only analysis for an assembled dashboard payload."""

    counts = structural_counts(payload)
    if not expected_shape(counts):
        raise ModernizationAuditError("BASELINE_STRUCTURE_MISMATCH")

    views = payload.get("views")
    if not isinstance(views, list) or not views:
        raise ModernizationAuditError("VIEW_STRUCTURE_UNAVAILABLE")

    overall = _new_totals()
    anonymous_views: list[dict[str, Any]] = []
    all_sections_view = True

    for view_index, view in enumerate(views):
        if not isinstance(view, dict):
            raise ModernizationAuditError("VIEW_STRUCTURE_UNAVAILABLE")

        is_sections = view.get("type") == "sections"
        all_sections_view = all_sections_view and is_sections

        sections = view.get("sections")
        if not isinstance(sections, list):
            sections = []

        view_totals = _new_totals()
        anonymous_sections: list[dict[str, Any]] = []

        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                raise ModernizationAuditError("SECTION_STRUCTURE_UNAVAILABLE")

            section_totals = _new_totals()
            cards = section.get("cards")
            if not isinstance(cards, list):
                cards = []

            for card in cards:
                if not isinstance(card, dict):
                    raise ModernizationAuditError("CARD_STRUCTURE_UNAVAILABLE")
                _add_card_metrics(section_totals, card)

            _merge_totals(view_totals, section_totals)

            anonymous_sections.append(
                {
                    "section": f"section_{section_index:02d}",
                    "top_level_card_count": section_totals["top_level_card_count"],
                    "top_level_custom_card_count": section_totals[
                        "top_level_custom_card_count"
                    ],
                    "top_level_grid_card_count": section_totals[
                        "top_level_grid_card_count"
                    ],
                    "top_level_stack_card_count": section_totals[
                        "top_level_stack_card_count"
                    ],
                    "explicit_grid_options_count": section_totals[
                        "top_level_explicit_grid_options_count"
                    ],
                    "grouping_nested_card_count": section_totals[
                        "grouping_nested_card_count"
                    ],
                }
            )

        _merge_totals(overall, view_totals)

        anonymous_views.append(
            {
                "view": f"view_{view_index:02d}",
                "is_sections": is_sections,
                "max_columns_class": _max_columns_class(view.get("max_columns")),
                "dense_section_placement": _dense_placement_class(view),
                "section_count": len(sections),
                "sections": anonymous_sections,
            }
        )

    invalid_layout = (
        overall["top_level_invalid_grid_options_count"] > 0
        or any(
            item["max_columns_class"] == "invalid"
            or item["dense_section_placement"] == "invalid"
            for item in anonymous_views
        )
    )

    grouping_layout_present = (
        overall["top_level_grid_card_count"] > 0
        or overall["top_level_stack_card_count"] > 0
        or overall["grouping_nested_grid_card_count"] > 0
        or overall["grouping_nested_stack_card_count"] > 0
    )

    custom_default_sizing_uncertain = overall["custom_default_grid_options_count"]

    reasons: list[str] = []
    if not all_sections_view:
        reasons.append("NON_SECTIONS_VIEW_PRESENT")
    if grouping_layout_present:
        reasons.append("GROUPING_LAYOUT_WRAPPER_PRESENT")
    if invalid_layout:
        reasons.append("LAYOUT_DECLARATION_NEEDS_REVIEW")
    if custom_default_sizing_uncertain:
        reasons.append("CUSTOM_CARD_DEFAULT_SIZING_UNCERTAIN")

    if invalid_layout:
        decision = "NEEDS_PRIVATE_REVIEW"
    elif not all_sections_view or grouping_layout_present:
        decision = "READY_FOR_BOUNDED_SECTIONS_MODERNIZATION_DESIGN"
    elif custom_default_sizing_uncertain:
        decision = "NEEDS_PRIVATE_REVIEW"
    else:
        decision = "SECTIONS_ALREADY_MODERN_NO_ACTION"

    return {
        "decision": decision,
        "reasons": reasons,
        "structure": counts,
        "layout": {
            "all_views_sections": all_sections_view,
            "view_count": len(anonymous_views),
            "views": anonymous_views,
            "top_level": {
                "card_count": overall["top_level_card_count"],
                "custom_card_count": overall["top_level_custom_card_count"],
                "grid_card_count": overall["top_level_grid_card_count"],
                "stack_card_count": overall["top_level_stack_card_count"],
                "explicit_grid_options_count": overall[
                    "top_level_explicit_grid_options_count"
                ],
                "default_grid_options_count": overall[
                    "top_level_default_grid_options_count"
                ],
                "full_width_count": overall["top_level_full_width_count"],
                "bounded_width_count": overall["top_level_bounded_width_count"],
                "unspecified_width_count": overall[
                    "top_level_unspecified_width_count"
                ],
                "invalid_grid_options_count": overall[
                    "top_level_invalid_grid_options_count"
                ],
            },
            "custom_cards": {
                "explicit_grid_options_count": overall[
                    "custom_explicit_grid_options_count"
                ],
                "default_grid_options_count": overall[
                    "custom_default_grid_options_count"
                ],
                "default_sizing_runtime_capability_unknown": (
                    overall["custom_default_grid_options_count"] > 0
                ),
            },
            "grouping_wrappers": {
                "nested_card_count": overall["grouping_nested_card_count"],
                "nested_custom_card_count": overall[
                    "grouping_nested_custom_card_count"
                ],
                "nested_grid_card_count": overall[
                    "grouping_nested_grid_card_count"
                ],
                "nested_stack_card_count": overall[
                    "grouping_nested_stack_card_count"
                ],
            },
        },
    }


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

        payload = load_candidate_tree(active_dashboard.parent)
        analysis = analyze_sections_layout(payload)

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
            "dashboard": {
                "structure": analysis["structure"],
                "layout": analysis["layout"],
            },
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }
    except ModernizationAuditError as exc:
        return blocked_report(exc.reason)
    except Exception:
        return blocked_report("MODERNIZATION_AUDIT_FAILED")


def running_home_assistant_version() -> str:
    try:
        from homeassistant.const import __version__
    except (ImportError, AttributeError) as exc:
        raise ModernizationAuditError("HOME_ASSISTANT_VERSION_UNAVAILABLE") from exc
    return str(__version__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the active modular Mājas dashboard for private-safe "
            "Home Assistant Sections modernization signals. The tool is read-only."
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
        except ModernizationAuditError as exc:
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
