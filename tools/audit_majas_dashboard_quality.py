#!/usr/bin/env python3
"""Audit the accepted Mājas dashboard against the current 2026.9 contract."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from tools import audit_majas_dashboard_quality_legacy as _legacy
from tools.materialize_majas_dashboard_candidate import load_candidate_tree
from tools.plan_majas_dashboard_activation import resolve_binding_owner

EXPECTED_HA_VERSION = "2026.9.3"
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"
BUTTON_CARD_BUNDLE_RELATIVE = Path("www/community/button-card/button-card.js")
ACTION_KEYS = ("tap_action", "hold_action", "double_tap_action")
FIXED_DIMENSION_KEYS = {"height", "width", "aspect-ratio", "aspect_ratio"}

DashboardQualityAuditError = _legacy.DashboardQualityAuditError
privacy_report = _legacy.privacy_report
mutation_report = _legacy.mutation_report
blocked_report = _legacy.blocked_report
inventory_active_tree = _legacy.inventory_active_tree
running_home_assistant_version = _legacy.running_home_assistant_version


def iter_top_level_cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _legacy._iter_top_level_cards(payload)


def _template_refs(card: dict[str, Any]) -> list[str]:
    raw = card.get("template")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    return []


def _root_fixed_dimensions(template: Any) -> set[str]:
    if not isinstance(template, dict):
        return set()
    styles = template.get("styles")
    if not isinstance(styles, dict):
        return set()
    card_styles = styles.get("card")
    if not isinstance(card_styles, list):
        return set()

    found: set[str] = set()
    for entry in card_styles:
        if not isinstance(entry, dict):
            continue
        for key in entry:
            if isinstance(key, str) and key in FIXED_DIMENSION_KEYS:
                found.add(key)
    return found


def _section_mode_class(card: dict[str, Any]) -> str:
    if "section_mode" not in card:
        return "missing"
    value = card.get("section_mode")
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "invalid"


def _count_key(value: Any, key_name: str) -> int:
    if isinstance(value, dict):
        return (1 if key_name in value else 0) + sum(
            _count_key(item, key_name) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_count_key(item, key_name) for item in value)
    return 0


def _action_schema_metrics(payload: dict[str, Any]) -> dict[str, int]:
    counts = {
        "perform_action": 0,
        "legacy_service": 0,
        "integration_event": 0,
        "informational": 0,
        "navigation": 0,
        "state_changing_other": 0,
        "unknown": 0,
    }

    def classify(action: Any) -> None:
        if not isinstance(action, dict):
            counts["unknown"] += 1
            return
        action_type = action.get("action")
        if action_type == "perform-action":
            counts["perform_action"] += 1
        elif action_type == "call-service":
            counts["legacy_service"] += 1
        elif action_type == "fire-dom-event":
            counts["integration_event"] += 1
        elif action_type in {"more-info", "none", "assist"}:
            counts["informational"] += 1
        elif action_type in {"navigate", "url"}:
            counts["navigation"] += 1
        elif action_type == "toggle":
            counts["state_changing_other"] += 1
        else:
            counts["unknown"] += 1

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ACTION_KEYS:
                    classify(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return counts


def _modernization_metrics(
    payload: dict[str, Any],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    templates_raw = payload.get("button_card_templates")
    templates = templates_raw if isinstance(templates_raw, dict) else {}

    section_mode = {"true": 0, "false": 0, "missing": 0, "invalid": 0}
    top_level_custom = 0
    fixed_template_names: set[str] = set()
    for name, template in templates.items():
        if isinstance(name, str) and _root_fixed_dimensions(template):
            fixed_template_names.add(name)

    top_level_with_fixed_template = 0
    for card in cards:
        if _legacy._is_custom(card):
            top_level_custom += 1
            section_mode[_section_mode_class(card)] += 1
        if fixed_template_names.intersection(_template_refs(card)):
            top_level_with_fixed_template += 1

    fixed_height_templates = 0
    fixed_width_templates = 0
    fixed_aspect_templates = 0
    for template in templates.values():
        dimensions = _root_fixed_dimensions(template)
        if "height" in dimensions:
            fixed_height_templates += 1
        if "width" in dimensions:
            fixed_width_templates += 1
        if {"aspect-ratio", "aspect_ratio"}.intersection(dimensions):
            fixed_aspect_templates += 1

    return {
        "top_level_custom_card_count": top_level_custom,
        "section_mode": {
            "true_count": section_mode["true"],
            "false_count": section_mode["false"],
            "missing_count": section_mode["missing"],
            "invalid_count": section_mode["invalid"],
        },
        "fixed_root_dimensions": {
            "template_count": len(fixed_template_names),
            "fixed_height_template_count": fixed_height_templates,
            "fixed_width_template_count": fixed_width_templates,
            "fixed_aspect_ratio_template_count": fixed_aspect_templates,
            "top_level_card_conflict_count": top_level_with_fixed_template,
        },
        "dead_configuration": {
            "triggers_update_declaration_count": _count_key(
                templates, "triggers_update"
            )
        },
        "action_schema": _action_schema_metrics(payload),
    }


def analyze_dashboard_quality(payload: dict[str, Any]) -> dict[str, Any]:
    report = copy.deepcopy(_legacy.analyze_dashboard_quality(payload))
    cards = iter_top_level_cards(payload)
    modernization = _modernization_metrics(payload, cards)

    if modernization["section_mode"]["invalid_count"]:
        raise DashboardQualityAuditError("SECTION_MODE_DECLARATION_INVALID")

    report["modernization"] = modernization
    return report


def classify_lovelace_mode(mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {
            "mapping_valid": False,
            "legacy_mode_present": False,
            "legacy_mode_class": "invalid",
        }
    if "mode" not in mapping:
        return {
            "mapping_valid": True,
            "legacy_mode_present": False,
            "legacy_mode_class": "missing",
        }

    value = mapping.get("mode")
    if value == "storage":
        mode_class = "legacy_storage"
    elif value == "yaml":
        mode_class = "legacy_yaml"
    else:
        mode_class = "other"

    return {
        "mapping_valid": True,
        "legacy_mode_present": True,
        "legacy_mode_class": mode_class,
    }


def _lovelace_mapping(owner_kind: str, owner_payload: dict[str, Any]) -> Any:
    if owner_kind == "LOVELACE_INCLUDE":
        return owner_payload
    if owner_kind == "CONFIGURATION_ROOT":
        return owner_payload.get("lovelace")
    return None


def detect_button_card_capability(config_root: Path) -> dict[str, Any]:
    try:
        root = config_root.resolve(strict=True)
    except OSError:
        return {
            "sections_sizing_capability": "unavailable",
            "triggers_update_runtime": "unknown",
        }

    candidate = root / BUTTON_CARD_BUNDLE_RELATIVE
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return {
            "sections_sizing_capability": "unavailable",
            "triggers_update_runtime": "unknown",
        }
    if root not in resolved.parents or not resolved.is_file():
        return {
            "sections_sizing_capability": "unavailable",
            "triggers_update_runtime": "unknown",
        }

    try:
        text = resolved.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return {
            "sections_sizing_capability": "unknown",
            "triggers_update_runtime": "unknown",
        }

    section_mode = "section_mode" in text
    grid_api = "getGridOptions" in text
    return {
        "sections_sizing_capability": (
            "proven" if section_mode and grid_api else "unknown"
        ),
        "triggers_update_runtime": (
            "present" if "triggers_update" in text else "absent"
        ),
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
            owner_payload,
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
        capability = detect_button_card_capability(root)
        lovelace = classify_lovelace_mode(
            _lovelace_mapping(owner_kind, owner_payload)
        )

        modernization = analysis["modernization"]
        reasons = list(analysis["reasons"])
        candidate_classes = list(analysis["candidate_classes"])

        if modernization["section_mode"]["missing_count"]:
            candidate_classes.append("SECTIONS_MODE")
        if modernization["fixed_root_dimensions"]["top_level_card_conflict_count"]:
            candidate_classes.append("SECTIONS_FIXED_DIMENSIONS")
        if (
            modernization["dead_configuration"]["triggers_update_declaration_count"]
            and capability["triggers_update_runtime"] == "absent"
        ):
            candidate_classes.append("DEAD_TRIGGERS_UPDATE")
        if modernization["action_schema"]["legacy_service"]:
            candidate_classes.append("ACTION_SCHEMA")
        if lovelace["legacy_mode_class"] == "legacy_storage":
            candidate_classes.append("LOVELACE_LEGACY_MODE")

        candidate_classes = list(dict.fromkeys(candidate_classes))
        if candidate_classes:
            decision = "READY_FOR_BOUNDED_DASHBOARD_QUALITY_PASS"
            reasons = ["EVIDENCE_BACKED_BOUNDED_CANDIDATE_PRESENT"]
        else:
            decision = analysis["decision"]

        return {
            "schema": 2,
            "decision": decision,
            "reasons": reasons,
            "candidate_classes": candidate_classes,
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
                "modernization": modernization,
            },
            "dependencies": {"button_card": capability},
            "lovelace": lovelace,
            "privacy": analysis["privacy"],
            "mutation": analysis["mutation"],
        }
    except DashboardQualityAuditError as exc:
        return blocked_report(exc.reason)
    except Exception:
        return blocked_report("DASHBOARD_QUALITY_AUDIT_FAILED")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the accepted Mājas dashboard for Home Assistant 2026.9 "
            "Sections and action modernization signals."
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
