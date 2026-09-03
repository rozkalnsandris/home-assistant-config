#!/usr/bin/env python3
"""Audit the accepted post-roadmap Mājas dashboard without exposing private data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.materialize_majas_dashboard_candidate import (
    load_candidate_tree,
    structural_counts,
)
from tools.plan_majas_dashboard_activation import (
    EXPECTED_CANDIDATE_DIRS,
    EXPECTED_CANDIDATE_FILES,
    resolve_binding_owner,
)

EXPECTED_HA_VERSION = "2026.8.2"
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"

EXPECTED_STRUCTURE = {
    "view_count": 1,
    "section_count": 3,
    "card_count": 11,
    "custom_card_count": 11,
    "distinct_custom_card_type_count": 1,
}
EXPECTED_TOP_LEVEL_CARD_COUNT = 11
EXPECTED_GROUPING_WRAPPER_COUNT = 0

GROUPING_TYPES = {"grid", "horizontal-stack", "vertical-stack"}
ACTION_KEYS = ("tap_action", "hold_action", "double_tap_action")
INFORMATIONAL_ACTIONS = {"more-info", "none", "assist"}
NAVIGATION_ACTIONS = {"navigate", "url"}
STATE_CHANGING_ACTIONS = {"toggle", "call-service", "perform-action"}
HIGHER_IMPACT_SERVICE_DOMAINS = {"alarm_control_panel", "lock"}


class DashboardQualityAuditError(RuntimeError):
    """Sanitized post-roadmap dashboard audit failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "binding_values_emitted": False,
        "entity_ids_emitted": False,
        "view_section_card_titles_emitted": False,
        "card_type_names_emitted": False,
        "custom_card_type_names_emitted": False,
        "actions_targets_services_or_urls_emitted": False,
        "schedule_values_emitted": False,
    }


def mutation_report() -> dict[str, bool]:
    return {
        "owner_file_modified": False,
        "dashboard_modified": False,
        "binding_changed": False,
        "storage_write": False,
        "helper_entity_or_device_mutation": False,
        "reload_or_restart": False,
        "host_runtime_mutation": False,
    }


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": [reason],
        "privacy": privacy_report(),
        "mutation": mutation_report(),
    }


def _is_custom(card: Any) -> bool:
    if not isinstance(card, dict):
        return False
    card_type = card.get("type")
    return isinstance(card_type, str) and card_type.startswith("custom:")


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


def _dense_section_placement_class(view: dict[str, Any]) -> str:
    if "dense_section_placement" not in view:
        return "unset"
    value = view.get("dense_section_placement")
    if value is True:
        return "enabled"
    if value is False:
        return "disabled"
    return "invalid"


def _grid_options_class(card: dict[str, Any]) -> tuple[str, str]:
    """Return (declaration class, width class) without exposing values."""

    if "grid_options" not in card:
        return "default", "default"

    options = card.get("grid_options")
    if not isinstance(options, dict):
        return "invalid", "invalid"

    columns = options.get("columns")
    if columns == "full":
        return "explicit", "full"
    if isinstance(columns, int) and not isinstance(columns, bool) and columns > 0:
        return "explicit", "bounded"
    if columns is None:
        return "explicit", "unspecified"
    return "invalid", "invalid"


def _iter_top_level_cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    views = payload.get("views")
    if not isinstance(views, list):
        raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")

    for view in views:
        if not isinstance(view, dict):
            raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")
        sections = view.get("sections")
        if not isinstance(sections, list):
            raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")
        for section in sections:
            if not isinstance(section, dict):
                raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")
            section_cards = section.get("cards")
            if not isinstance(section_cards, list):
                raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")
            for card in section_cards:
                if not isinstance(card, dict):
                    raise DashboardQualityAuditError(
                        "POST_ROADMAP_STRUCTURE_MISMATCH"
                    )
                cards.append(card)
    return cards


def _walk_card(card: dict[str, Any]):
    yield card
    children = card.get("cards")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                raise DashboardQualityAuditError(
                    "POST_ROADMAP_STRUCTURE_MISMATCH"
                )
            yield from _walk_card(child)
    child = card.get("card")
    if child is not None:
        if not isinstance(child, dict):
            raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")
        yield from _walk_card(child)


def _grouping_wrapper_count(cards: list[dict[str, Any]]) -> int:
    count = 0
    for top_level in cards:
        for card in _walk_card(top_level):
            card_type = card.get("type")
            if isinstance(card_type, str) and card_type in GROUPING_TYPES:
                count += 1
    return count


def _layout_metrics(
    payload: dict[str, Any], cards: list[dict[str, Any]]
) -> dict[str, Any]:
    views = payload.get("views")
    assert isinstance(views, list)

    anonymous_views: list[dict[str, Any]] = []
    all_views_sections = True
    native_header_count = 0
    view_badge_count = 0

    for index, raw_view in enumerate(views):
        if not isinstance(raw_view, dict):
            raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")
        is_sections = raw_view.get("type") == "sections"
        all_views_sections = all_views_sections and is_sections

        max_columns = _max_columns_class(raw_view.get("max_columns"))
        dense = _dense_section_placement_class(raw_view)
        if max_columns == "invalid" or dense == "invalid":
            raise DashboardQualityAuditError("LAYOUT_DECLARATION_INVALID")

        header = raw_view.get("header")
        if isinstance(header, dict):
            native_header_count += 1

        badges = raw_view.get("badges")
        if badges is not None and not isinstance(badges, list):
            raise DashboardQualityAuditError("LAYOUT_DECLARATION_INVALID")
        if isinstance(badges, list):
            view_badge_count += len(badges)

        sections = raw_view.get("sections")
        if not isinstance(sections, list):
            raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")

        anonymous_views.append(
            {
                "view": f"view_{index:02d}",
                "is_sections": is_sections,
                "section_count": len(sections),
                "max_columns_class": max_columns,
                "dense_section_placement": dense,
                "native_header_present": isinstance(header, dict),
                "badge_count": len(badges) if isinstance(badges, list) else 0,
            }
        )

    declaration_counts = {"explicit": 0, "default": 0, "invalid": 0}
    width_counts = {
        "full": 0,
        "bounded": 0,
        "default": 0,
        "unspecified": 0,
        "invalid": 0,
    }
    custom_declaration_counts = {"explicit": 0, "default": 0, "invalid": 0}

    native_heading_card_count = 0
    native_heading_badge_count = 0
    native_tile_card_count = 0

    for card in cards:
        declaration, width = _grid_options_class(card)
        declaration_counts[declaration] += 1
        width_counts[width] += 1

        if _is_custom(card):
            custom_declaration_counts[declaration] += 1

        card_type = card.get("type")
        if card_type == "heading":
            native_heading_card_count += 1
            badges = card.get("badges")
            if badges is not None and not isinstance(badges, list):
                raise DashboardQualityAuditError("LAYOUT_DECLARATION_INVALID")
            if isinstance(badges, list):
                native_heading_badge_count += len(badges)
        elif card_type == "tile":
            native_tile_card_count += 1

    if declaration_counts["invalid"] or width_counts["invalid"]:
        raise DashboardQualityAuditError("LAYOUT_DECLARATION_INVALID")

    return {
        "all_views_sections": all_views_sections,
        "views": anonymous_views,
        "top_level_card_count": len(cards),
        "grouping_wrapper_count": _grouping_wrapper_count(cards),
        "grid_options": {
            "explicit_count": declaration_counts["explicit"],
            "default_count": declaration_counts["default"],
            "invalid_count": declaration_counts["invalid"],
        },
        "width": {
            "full_count": width_counts["full"],
            "bounded_count": width_counts["bounded"],
            "default_count": width_counts["default"],
            "unspecified_count": width_counts["unspecified"],
            "invalid_count": width_counts["invalid"],
        },
        "custom_cards": {
            "explicit_grid_options_count": custom_declaration_counts["explicit"],
            "default_grid_options_count": custom_declaration_counts["default"],
            "invalid_grid_options_count": custom_declaration_counts["invalid"],
            "runtime_sections_sizing_capability": (
                "unknown"
                if any(_is_custom(card) for card in cards)
                else "unavailable"
            ),
            "automatic_sizing_rewrite_authorized": False,
        },
        "native_components": {
            "view_header_count": native_header_count,
            "view_badge_count": view_badge_count,
            "heading_card_count": native_heading_card_count,
            "heading_badge_count": native_heading_badge_count,
            "tile_card_count": native_tile_card_count,
        },
        "native_replacement": {
            "eligible_count": 0,
            "unknown_count": sum(1 for card in cards if _is_custom(card)),
            "automatic_conversion_authorized": False,
        },
    }


def _service_domain(action: dict[str, Any]) -> str | None:
    value = action.get("perform_action")
    if not isinstance(value, str):
        value = action.get("service")
    if not isinstance(value, str) or "." not in value:
        return None
    return value.split(".", 1)[0]


def _classify_action(action: Any) -> tuple[str, str]:
    """Return (surface class, impact class) without returning private values."""

    if not isinstance(action, dict):
        return "unknown", "unknown"

    action_type = action.get("action")
    if not isinstance(action_type, str):
        return "unknown", "unknown"

    if action_type in INFORMATIONAL_ACTIONS:
        return "informational", "not_state_changing"
    if action_type in NAVIGATION_ACTIONS:
        return "navigation", "not_state_changing"
    if action_type in STATE_CHANGING_ACTIONS:
        if action_type == "toggle":
            return "state_changing", "unknown"
        domain = _service_domain(action)
        if domain in HIGHER_IMPACT_SERVICE_DOMAINS:
            return "state_changing", "higher_impact"
        return "state_changing", "unknown"
    return "unknown", "unknown"


def _action_metrics(cards: list[dict[str, Any]]) -> dict[str, Any]:
    surfaces = {
        "informational": 0,
        "navigation": 0,
        "state_changing": 0,
        "unknown": 0,
    }
    higher_impact = 0
    state_change_impact_unknown = 0
    state_changing_with_confirmation = 0
    higher_impact_with_confirmation = 0
    state_changing_tap = 0
    state_changing_hold = 0
    state_changing_double_tap = 0
    state_changing_tap_with_hold_alternative = 0
    state_changing_tap_with_double_tap_alternative = 0

    for top_level in cards:
        for card in _walk_card(top_level):
            hold_present = isinstance(card.get("hold_action"), dict)
            double_present = isinstance(card.get("double_tap_action"), dict)

            for key in ACTION_KEYS:
                if key not in card:
                    continue
                action = card.get(key)
                surface, impact = _classify_action(action)
                surfaces[surface] += 1

                if surface != "state_changing":
                    continue

                if impact == "higher_impact":
                    higher_impact += 1
                else:
                    state_change_impact_unknown += 1

                if isinstance(action, dict) and "confirmation" in action:
                    state_changing_with_confirmation += 1
                    if impact == "higher_impact":
                        higher_impact_with_confirmation += 1

                if key == "tap_action":
                    state_changing_tap += 1
                    if hold_present:
                        state_changing_tap_with_hold_alternative += 1
                    if double_present:
                        state_changing_tap_with_double_tap_alternative += 1
                elif key == "hold_action":
                    state_changing_hold += 1
                elif key == "double_tap_action":
                    state_changing_double_tap += 1

    unguarded_higher_impact = max(
        0, higher_impact - higher_impact_with_confirmation
    )

    return {
        "surface_counts": surfaces,
        "higher_impact_state_changing_count": higher_impact,
        "state_changing_impact_unknown_count": state_change_impact_unknown,
        "confirmation_coverage_count": state_changing_with_confirmation,
        "state_changing_tap_count": state_changing_tap,
        "state_changing_hold_count": state_changing_hold,
        "state_changing_double_tap_count": state_changing_double_tap,
        "state_changing_tap_with_hold_alternative_count": (
            state_changing_tap_with_hold_alternative
        ),
        "state_changing_tap_with_double_tap_alternative_count": (
            state_changing_tap_with_double_tap_alternative
        ),
        "unguarded_higher_impact_count": unguarded_higher_impact,
    }


def inventory_active_tree(active_root: Path) -> dict[str, Any]:
    entries = list(active_root.rglob("*"))
    symlinks = [item for item in entries if item.is_symlink()]
    files = {
        item.relative_to(active_root).as_posix()
        for item in entries
        if item.is_file() and not item.is_symlink()
    }
    dirs = {
        item.relative_to(active_root).as_posix()
        for item in entries
        if item.is_dir() and not item.is_symlink()
    }

    unexpected = (files - EXPECTED_CANDIDATE_FILES) | (
        dirs - EXPECTED_CANDIDATE_DIRS
    )
    missing = (EXPECTED_CANDIDATE_FILES - files) | (
        EXPECTED_CANDIDATE_DIRS - dirs
    )

    return {
        "file_count": len(files),
        "directory_count": len(dirs),
        "symlink_count": len(symlinks),
        "unexpected_entry_count": len(unexpected),
        "missing_expected_entry_count": len(missing),
        "exact_expected_tree": not symlinks and not unexpected and not missing,
    }


def analyze_dashboard_quality(payload: dict[str, Any]) -> dict[str, Any]:
    structure = structural_counts(payload)
    if structure != EXPECTED_STRUCTURE:
        raise DashboardQualityAuditError("POST_ROADMAP_STRUCTURE_MISMATCH")

    cards = _iter_top_level_cards(payload)
    layout = _layout_metrics(payload, cards)

    if not layout["all_views_sections"]:
        raise DashboardQualityAuditError("POST_ROADMAP_LAYOUT_BASELINE_MISMATCH")
    if layout["top_level_card_count"] != EXPECTED_TOP_LEVEL_CARD_COUNT:
        raise DashboardQualityAuditError("POST_ROADMAP_LAYOUT_BASELINE_MISMATCH")
    if layout["grouping_wrapper_count"] != EXPECTED_GROUPING_WRAPPER_COUNT:
        raise DashboardQualityAuditError("POST_ROADMAP_LAYOUT_BASELINE_MISMATCH")

    actions = _action_metrics(cards)

    candidate_classes: list[str] = []
    if actions["unguarded_higher_impact_count"] > 0:
        candidate_classes.append("ACTION_SAFETY")

    if candidate_classes:
        decision = "READY_FOR_BOUNDED_DASHBOARD_QUALITY_PASS"
        reasons = ["EVIDENCE_BACKED_BOUNDED_CANDIDATE_PRESENT"]
    else:
        decision = "DASHBOARD_CURRENTLY_OPTIMAL_NO_CHANGE"
        reasons = ["NO_EVIDENCE_BACKED_BOUNDED_CHANGE"]

    return {
        "decision": decision,
        "reasons": reasons,
        "candidate_classes": candidate_classes,
        "structure": structure,
        "layout": layout,
        "actions": actions,
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

        active_root = active_dashboard.parent
        tree = inventory_active_tree(active_root)
        if not tree["exact_expected_tree"]:
            return blocked_report("ACTIVE_MODULAR_TREE_MISMATCH")

        payload = load_candidate_tree(active_root)
        analysis = analyze_dashboard_quality(payload)

        return {
            "schema": 1,
            "decision": analysis["decision"],
            "reasons": analysis["reasons"],
            "candidate_classes": analysis["candidate_classes"],
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
                "tree": tree,
                "structure": analysis["structure"],
                "layout": analysis["layout"],
                "actions": analysis["actions"],
            },
            "privacy": analysis["privacy"],
            "mutation": analysis["mutation"],
        }
    except DashboardQualityAuditError as exc:
        return blocked_report(exc.reason)
    except Exception:
        return blocked_report("DASHBOARD_QUALITY_AUDIT_FAILED")


def running_home_assistant_version() -> str:
    try:
        from homeassistant.const import __version__
    except (ImportError, AttributeError) as exc:
        raise DashboardQualityAuditError(
            "HOME_ASSISTANT_VERSION_UNAVAILABLE"
        ) from exc
    return str(__version__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the accepted post-roadmap Mājas dashboard for private-safe "
            "quality, responsive-layout and action-safety signals."
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
        except DashboardQualityAuditError as exc:
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
