#!/usr/bin/env python3
"""Plan the bounded private Mājas dashboard modernization for Home Assistant 2026.9."""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.audit_majas_dashboard_quality import (
    ACTION_KEYS,
    EXPECTED_HA_VERSION,
    analyze_dashboard_quality,
    classify_lovelace_mode,
    detect_button_card_capability,
    iter_top_level_cards,
)
from tools.materialize_majas_dashboard_candidate import load_candidate_tree
from tools.plan_majas_dashboard_activation import resolve_binding_owner

DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"
FIXED_DIMENSION_KEYS = {"height", "width", "aspect-ratio", "aspect_ratio"}


class ModernizationPlanError(RuntimeError):
    """Sanitized fail-closed planning error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ModernizationPlan:
    """Keep private candidates in memory and expose only a sanitized report."""

    dashboard_candidate: dict[str, Any]
    lovelace_candidate: dict[str, Any]
    report: dict[str, Any]


def _privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "entity_ids_emitted": False,
        "titles_or_names_emitted": False,
        "actions_targets_services_or_urls_emitted": False,
        "schedule_values_emitted": False,
    }


def _mutation_report() -> dict[str, bool]:
    return {
        "dashboard_modified": False,
        "lovelace_owner_modified": False,
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
        "privacy": _privacy_report(),
        "mutation": _mutation_report(),
    }


def _template_refs(card: dict[str, Any]) -> list[str]:
    raw = card.get("template")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    return []


def _all_template_usage(payload: Any) -> dict[str, int]:
    counts: dict[str, int] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            raw = value.get("template")
            refs: list[str] = []
            if isinstance(raw, str):
                refs = [raw]
            elif isinstance(raw, list):
                refs = [item for item in raw if isinstance(item, str)]
            for ref in refs:
                counts[ref] = counts.get(ref, 0) + 1
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return counts


def _top_level_template_usage(cards: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in cards:
        for ref in _template_refs(card):
            counts[ref] = counts.get(ref, 0) + 1
    return counts


def _fixed_dimension_keys(template: Any) -> set[str]:
    if not isinstance(template, dict):
        return set()
    styles = template.get("styles")
    if not isinstance(styles, dict):
        return set()
    card_styles = styles.get("card")
    if not isinstance(card_styles, list):
        return set()
    result: set[str] = set()
    for entry in card_styles:
        if not isinstance(entry, dict):
            continue
        result.update(
            key
            for key in entry
            if isinstance(key, str) and key in FIXED_DIMENSION_KEYS
        )
    return result


def _remove_fixed_dimensions(template: dict[str, Any]) -> int:
    styles = template.get("styles")
    if not isinstance(styles, dict):
        return 0
    card_styles = styles.get("card")
    if not isinstance(card_styles, list):
        return 0

    removed = 0
    replacement: list[Any] = []
    for entry in card_styles:
        if not isinstance(entry, dict):
            replacement.append(entry)
            continue
        cleaned: dict[Any, Any] = {}
        for key, value in entry.items():
            if isinstance(key, str) and key in FIXED_DIMENSION_KEYS:
                removed += 1
                continue
            cleaned[key] = value
        if cleaned:
            replacement.append(cleaned)
    styles["card"] = replacement
    return removed


def _remove_key_recursive(value: Any, key_name: str) -> int:
    removed = 0
    if isinstance(value, dict):
        if key_name in value:
            value.pop(key_name)
            removed += 1
        for item in value.values():
            removed += _remove_key_recursive(item, key_name)
    elif isinstance(value, list):
        for item in value:
            removed += _remove_key_recursive(item, key_name)
    return removed


def _convert_action_map(action: dict[str, Any]) -> bool:
    if action.get("action") != "call-service":
        return False

    service = action.get("service")
    if not isinstance(service, str) or not service:
        raise ModernizationPlanError("LEGACY_ACTION_SERVICE_UNPROVEN")
    if "perform_action" in action:
        raise ModernizationPlanError("LEGACY_ACTION_COLLISION")
    if "service_data" in action and "data" in action:
        raise ModernizationPlanError("LEGACY_ACTION_COLLISION")

    converted: dict[str, Any] = {}
    for key, value in action.items():
        if key == "action":
            converted["action"] = "perform-action"
        elif key == "service":
            converted["perform_action"] = value
        elif key == "service_data":
            if value is not None and not isinstance(value, dict):
                raise ModernizationPlanError("LEGACY_ACTION_DATA_UNPROVEN")
            converted["data"] = value
        else:
            converted[key] = value
    action.clear()
    action.update(converted)
    return True


def _convert_legacy_actions(value: Any) -> int:
    converted = 0
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key in ACTION_KEYS and isinstance(item, dict):
                if _convert_action_map(item):
                    converted += 1
            converted += _convert_legacy_actions(item)
    elif isinstance(value, list):
        for item in value:
            converted += _convert_legacy_actions(item)
    return converted


def _dashboard_registry_is_yaml(mapping: dict[str, Any]) -> bool:
    dashboards = mapping.get("dashboards")
    if not isinstance(dashboards, dict) or not dashboards:
        return False
    for definition in dashboards.values():
        if not isinstance(definition, dict):
            return False
        if definition.get("mode") != "yaml":
            return False
        filename = definition.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            return False
    return True


def plan_modernization(
    payload: dict[str, Any],
    lovelace_mapping: dict[str, Any],
    *,
    sections_sizing_capability: str,
    triggers_update_runtime: str,
    candidate_validation_passed: bool = False,
) -> ModernizationPlan:
    analysis = analyze_dashboard_quality(payload)
    cards = iter_top_level_cards(payload)

    if analysis["structure"] != {
        "view_count": 1,
        "section_count": 3,
        "card_count": 11,
        "custom_card_count": 11,
        "distinct_custom_card_type_count": 1,
    }:
        raise ModernizationPlanError("POST_ROADMAP_STRUCTURE_MISMATCH")
    if not analysis["layout"]["all_views_sections"]:
        raise ModernizationPlanError("POST_ROADMAP_LAYOUT_BASELINE_MISMATCH")

    dashboard_candidate = copy.deepcopy(payload)
    candidate_cards = iter_top_level_cards(dashboard_candidate)
    templates_raw = dashboard_candidate.get("button_card_templates")
    templates = templates_raw if isinstance(templates_raw, dict) else {}

    delta = {
        "section_mode_enabled_count": 0,
        "fixed_dimension_entries_removed_count": 0,
        "triggers_update_removed_count": 0,
        "legacy_actions_converted_count": 0,
        "legacy_lovelace_mode_removed_count": 0,
        "grid_options_added_count": 0,
    }

    section_metrics = analysis["modernization"]["section_mode"]
    need_sections = (
        section_metrics["missing_count"] > 0
        or section_metrics["false_count"] > 0
        or analysis["modernization"]["fixed_root_dimensions"][
            "top_level_card_conflict_count"
        ]
        > 0
    )
    if section_metrics["invalid_count"]:
        raise ModernizationPlanError("SECTION_MODE_DECLARATION_INVALID")
    if section_metrics["false_count"]:
        raise ModernizationPlanError("EXPLICIT_SECTION_MODE_FALSE_REQUIRES_REVIEW")
    if need_sections and sections_sizing_capability != "proven":
        raise ModernizationPlanError("SECTIONS_SIZING_CAPABILITY_UNPROVEN")

    for card in candidate_cards:
        if not isinstance(card.get("type"), str) or not card["type"].startswith(
            "custom:"
        ):
            continue
        if "section_mode" not in card:
            card["section_mode"] = True
            delta["section_mode_enabled_count"] += 1

    all_usage = _all_template_usage(payload)
    top_usage = _top_level_template_usage(cards)
    targeted_templates: set[str] = set()
    for card in cards:
        targeted_templates.update(_template_refs(card))

    for name, template in templates.items():
        if not isinstance(name, str) or name not in targeted_templates:
            continue
        dimensions = _fixed_dimension_keys(template)
        if not dimensions:
            continue
        if all_usage.get(name, 0) != top_usage.get(name, 0):
            raise ModernizationPlanError("FIXED_DIMENSION_TEMPLATE_REUSE_UNPROVEN")
        if not isinstance(template, dict):
            raise ModernizationPlanError("FIXED_DIMENSION_TEMPLATE_INVALID")
        delta["fixed_dimension_entries_removed_count"] += _remove_fixed_dimensions(
            template
        )

    triggers_count = analysis["modernization"]["dead_configuration"][
        "triggers_update_declaration_count"
    ]
    if triggers_count:
        if triggers_update_runtime != "absent":
            raise ModernizationPlanError("TRIGGERS_UPDATE_RUNTIME_UNPROVEN")
        delta["triggers_update_removed_count"] = _remove_key_recursive(
            templates, "triggers_update"
        )
        if delta["triggers_update_removed_count"] != triggers_count:
            raise ModernizationPlanError("TRIGGERS_UPDATE_COUNT_MISMATCH")

    delta["legacy_actions_converted_count"] = _convert_legacy_actions(
        dashboard_candidate
    )

    lovelace_candidate = copy.deepcopy(lovelace_mapping)
    mode = classify_lovelace_mode(lovelace_candidate)
    if mode["legacy_mode_class"] == "legacy_storage":
        if not _dashboard_registry_is_yaml(lovelace_candidate):
            raise ModernizationPlanError("LOVELACE_MODE_REMOVAL_UNPROVEN")
        lovelace_candidate.pop("mode")
        delta["legacy_lovelace_mode_removed_count"] = 1
    elif mode["legacy_mode_class"] not in {"missing"}:
        raise ModernizationPlanError("LOVELACE_MODE_REMOVAL_UNPROVEN")

    total_changes = sum(delta.values())
    if total_changes == 0:
        decision = "DASHBOARD_2026_9_CURRENTLY_ALIGNED_NO_CHANGE"
        reasons = ["NO_EVIDENCE_BACKED_BOUNDED_CHANGE"]
    elif candidate_validation_passed:
        decision = "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY"
        reasons = ["BOUNDED_MODERNIZATION_CANDIDATE_VALIDATED"]
    else:
        decision = "NEEDS_PRIVATE_REVIEW"
        reasons = ["PRIVATE_FULL_CANDIDATE_VALIDATION_REQUIRED"]

    report = {
        "schema": 1,
        "decision": decision,
        "reasons": reasons,
        "planned_delta": delta,
        "candidate_validation_passed": bool(candidate_validation_passed),
        "privacy": _privacy_report(),
        "mutation": _mutation_report(),
    }
    return ModernizationPlan(
        dashboard_candidate=dashboard_candidate,
        lovelace_candidate=lovelace_candidate,
        report=report,
    )


def _lovelace_mapping(owner_kind: str, owner_payload: dict[str, Any]) -> Any:
    if owner_kind == "LOVELACE_INCLUDE":
        return owner_payload
    if owner_kind == "CONFIGURATION_ROOT":
        return owner_payload.get("lovelace")
    return None


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
            owner_payload,
            _dashboard_key,
            _definition,
            active_dashboard,
        ) = resolve_binding_owner(root, dashboard_title)
        payload = load_candidate_tree(active_dashboard.parent)
        lovelace = _lovelace_mapping(owner_kind, owner_payload)
        if not isinstance(lovelace, dict):
            raise ModernizationPlanError("LOVELACE_MAPPING_UNAVAILABLE")
        capability = detect_button_card_capability(root)
        plan = plan_modernization(
            payload,
            lovelace,
            sections_sizing_capability=capability[
                "sections_sizing_capability"
            ],
            triggers_update_runtime=capability["triggers_update_runtime"],
            candidate_validation_passed=False,
        )
        return {
            **plan.report,
            "home_assistant": {
                "expected_version": expected_version,
                "running_version": running_version,
                "version_match": True,
            },
            "binding": {"resolved": True, "owner_kind": owner_kind},
            "capability": capability,
        }
    except ModernizationPlanError as exc:
        return blocked_report(exc.reason)
    except Exception:
        return blocked_report("DASHBOARD_2026_9_PLAN_FAILED")


def running_home_assistant_version() -> str:
    try:
        from homeassistant.const import __version__
    except (ImportError, AttributeError) as exc:
        raise ModernizationPlanError("HOME_ASSISTANT_VERSION_UNAVAILABLE") from exc
    return str(__version__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan the bounded Mājas dashboard 2026.9 modernization."
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
        except ModernizationPlanError as exc:
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
