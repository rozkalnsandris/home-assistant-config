#!/usr/bin/env python3
"""Build a fail-closed in-memory Mājas 2026.9 modernization candidate."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from tools.audit_majas_dashboard_quality import analyze_dashboard_quality


class Majas20269PlanError(RuntimeError):
    """Sanitized planner failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PrivateModernizationPlan:
    dashboard: dict[str, Any]
    lovelace: dict[str, Any]
    report: dict[str, Any]


def _privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "binding_values_emitted": False,
        "entity_ids_emitted": False,
        "titles_or_names_emitted": False,
        "custom_card_identifiers_emitted": False,
        "actions_targets_services_or_urls_emitted": False,
        "schedule_values_emitted": False,
    }


def _mutation_report() -> dict[str, bool]:
    return {
        "live_dashboard_modified": False,
        "live_configuration_modified": False,
        "storage_write": False,
        "helper_entity_or_device_mutation": False,
        "reload_or_restart": False,
        "host_runtime_mutation": False,
    }


def _iter_top_level_cards(payload: dict[str, Any]):
    views = payload.get("views")
    if not isinstance(views, list):
        raise Majas20269PlanError("POST_ROADMAP_STRUCTURE_MISMATCH")
    for view in views:
        if not isinstance(view, dict) or view.get("type") != "sections":
            raise Majas20269PlanError("NATIVE_SECTIONS_REQUIRED")
        sections = view.get("sections")
        if not isinstance(sections, list):
            raise Majas20269PlanError("POST_ROADMAP_STRUCTURE_MISMATCH")
        for section in sections:
            if not isinstance(section, dict):
                raise Majas20269PlanError("POST_ROADMAP_STRUCTURE_MISMATCH")
            cards = section.get("cards")
            if not isinstance(cards, list):
                raise Majas20269PlanError("POST_ROADMAP_STRUCTURE_MISMATCH")
            for card in cards:
                if not isinstance(card, dict):
                    raise Majas20269PlanError("POST_ROADMAP_STRUCTURE_MISMATCH")
                yield card


def _is_custom(card: dict[str, Any]) -> bool:
    value = card.get("type")
    return isinstance(value, str) and value.startswith("custom:")


def _template_references(card: dict[str, Any]) -> list[str]:
    value = card.get("template")
    if value is None:
        return []
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return list(value)
    raise Majas20269PlanError("CUSTOM_CARD_TEMPLATE_REFERENCE_INVALID")


def _strip_template_root_dimensions(payload: dict[str, Any]) -> int:
    templates = payload.get("button_card_templates")
    referenced = {
        name
        for card in _iter_top_level_cards(payload)
        if _is_custom(card)
        for name in _template_references(card)
    }
    if templates is None:
        if referenced:
            raise Majas20269PlanError("CUSTOM_CARD_TEMPLATE_UNAVAILABLE")
        return 0
    if not isinstance(templates, dict):
        raise Majas20269PlanError("CUSTOM_CARD_TEMPLATE_INVALID")
    if not referenced.issubset(templates):
        raise Majas20269PlanError("CUSTOM_CARD_TEMPLATE_UNAVAILABLE")
    removed = 0
    for name in referenced:
        template = templates[name]
        if not isinstance(template, dict):
            raise Majas20269PlanError("CUSTOM_CARD_TEMPLATE_INVALID")
        styles = template.get("styles")
        if not isinstance(styles, dict):
            continue
        card_styles = styles.get("card")
        if card_styles is None:
            continue
        if not isinstance(card_styles, list):
            raise Majas20269PlanError("CUSTOM_CARD_TEMPLATE_INVALID")
        cleaned: list[dict[str, Any]] = []
        for item in card_styles:
            if not isinstance(item, dict):
                raise Majas20269PlanError("CUSTOM_CARD_TEMPLATE_INVALID")
            updated = copy.deepcopy(item)
            for key in ("height", "width"):
                if key in updated:
                    del updated[key]
                    removed += 1
            if updated:
                cleaned.append(updated)
        styles["card"] = cleaned
    return removed


def _remove_key_recursive(value: Any, key: str) -> int:
    removed = 0
    if isinstance(value, dict):
        if key in value:
            del value[key]
            removed += 1
        for item in list(value.values()):
            removed += _remove_key_recursive(item, key)
    elif isinstance(value, list):
        for item in value:
            removed += _remove_key_recursive(item, key)
    return removed


def _modernize_actions(value: Any) -> int:
    changed = 0
    if isinstance(value, dict):
        if value.get("action") == "call-service":
            service = value.get("service")
            if not isinstance(service, str) or not service:
                raise Majas20269PlanError("LEGACY_ACTION_NOT_EXACT")
            if "perform_action" in value:
                raise Majas20269PlanError("LEGACY_ACTION_NOT_EXACT")
            if "service_data" in value and "data" in value:
                raise Majas20269PlanError("LEGACY_ACTION_NOT_EXACT")
            value["action"] = "perform-action"
            value["perform_action"] = value.pop("service")
            if "service_data" in value:
                value["data"] = value.pop("service_data")
            changed += 1
        for item in list(value.values()):
            changed += _modernize_actions(item)
    elif isinstance(value, list):
        for item in value:
            changed += _modernize_actions(item)
    return changed


def _remove_legacy_lovelace_mode(lovelace: dict[str, Any]) -> int:
    if "mode" not in lovelace:
        return 0
    if not isinstance(lovelace.get("dashboards"), dict):
        raise Majas20269PlanError("LOVELACE_DASHBOARD_REGISTRY_REQUIRED")
    mode = lovelace.get("mode")
    if mode not in {"storage", "yaml"}:
        raise Majas20269PlanError("LOVELACE_LEGACY_MODE_INVALID")
    del lovelace["mode"]
    return 1


def build_private_plan(
    payload: dict[str, Any],
    lovelace_mapping: dict[str, Any],
    *,
    custom_card_sections_capability: str,
    triggers_update_runtime: str,
) -> PrivateModernizationPlan:
    """Return a private in-memory candidate and sanitized delta report."""

    if not isinstance(lovelace_mapping, dict):
        raise Majas20269PlanError("LOVELACE_MAPPING_UNAVAILABLE")
    legacy_mode_present = "mode" in lovelace_mapping
    audit = analyze_dashboard_quality(
        payload,
        custom_card_sections_capability=custom_card_sections_capability,
        triggers_update_runtime=triggers_update_runtime,
        legacy_lovelace_mode_present=legacy_mode_present,
    )
    if audit.get("decision") != "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY":
        raise Majas20269PlanError("AUDIT_NOT_READY_FOR_BOUNDED_PLAN")

    dashboard = copy.deepcopy(payload)
    lovelace = copy.deepcopy(lovelace_mapping)
    candidate_classes = set(audit.get("candidate_classes", []))

    section_mode_changes = 0
    if "SECTIONS_SIZING" in candidate_classes:
        if custom_card_sections_capability != "proven":
            raise Majas20269PlanError("CUSTOM_CARD_SECTIONS_CAPABILITY_NOT_PROVEN")
        for card in _iter_top_level_cards(dashboard):
            if not _is_custom(card):
                raise Majas20269PlanError("TOP_LEVEL_CUSTOM_CARD_BASELINE_MISMATCH")
            if card.get("section_mode") is not True:
                card["section_mode"] = True
                section_mode_changes += 1
        root_dimension_removals = _strip_template_root_dimensions(dashboard)
    else:
        root_dimension_removals = 0

    triggers_removed = 0
    if "DEAD_TRIGGERS_UPDATE" in candidate_classes:
        if triggers_update_runtime != "absent":
            raise Majas20269PlanError("TRIGGERS_UPDATE_RUNTIME_NOT_PROVEN_ABSENT")
        triggers_removed = _remove_key_recursive(dashboard, "triggers_update")

    action_changes = 0
    if "ACTION_SYNTAX" in candidate_classes:
        action_changes = _modernize_actions(dashboard)

    legacy_mode_removals = 0
    if "LOVELACE_LEGACY_MODE" in candidate_classes:
        legacy_mode_removals = _remove_legacy_lovelace_mode(lovelace)

    if any("grid_options" in card for card in _iter_top_level_cards(dashboard)):
        before_grid = audit["layout"]["grid_options"]["explicit_count"]
        after_grid = sum(
            1 for card in _iter_top_level_cards(dashboard) if "grid_options" in card
        )
        if before_grid != after_grid:
            raise Majas20269PlanError("GRID_OPTIONS_GUESS_FORBIDDEN")

    report = {
        "schema": 1,
        "decision": "NEEDS_PRIVATE_REVIEW",
        "reasons": ["PRIVATE_FULL_CANDIDATE_VALIDATION_REQUIRED"],
        "candidate_classes": sorted(candidate_classes),
        "planned_delta": {
            "top_level_section_mode_changes": section_mode_changes,
            "root_dimension_declarations_removed": root_dimension_removals,
            "triggers_update_declarations_removed": triggers_removed,
            "legacy_service_actions_modernized": action_changes,
            "legacy_lovelace_mode_entries_removed": legacy_mode_removals,
            "grid_options_added": 0,
        },
        "privacy": _privacy_report(),
        "mutation": _mutation_report(),
    }
    return PrivateModernizationPlan(dashboard, lovelace, report)


def finalize_private_plan(
    plan: PrivateModernizationPlan,
    *,
    candidate_validation_passed: bool,
) -> dict[str, Any]:
    report = copy.deepcopy(plan.report)
    if candidate_validation_passed:
        report["decision"] = "READY_FOR_BOUNDED_MAJAS_2026_9_APPLY"
        report["reasons"] = ["PRIVATE_FULL_CANDIDATE_VALIDATION_PASSED"]
    else:
        report["decision"] = "BLOCKED"
        report["reasons"] = ["PRIVATE_FULL_CANDIDATE_VALIDATION_FAILED"]
    return report
