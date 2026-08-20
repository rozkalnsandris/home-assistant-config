#!/usr/bin/env python3
"""Restart-aware privacy-safe RETIRE reconciliation after the #112 incident.

This module deliberately leaves the historical pre-#112 planner untouched.  The
canonical hardened RETIRE launcher installs this policy only for the post-#112
path, where the owner has already chosen RETIRE and #113 V8 proved the bounded
heater files plus empty Scheduler storage were restored exactly.
"""

from __future__ import annotations

from typing import Any

from tools import plan_heater_disabled_legacy_transition as _planner
from tools import reconcile_heater_binding_template_privately as _binding

READY = _planner.READY
BLOCKED = _planner.BLOCKED
SAFE_POST_RESTART_HELPER_STATES = frozenset({"off", "unknown", "unavailable"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _all_false(section: Any) -> bool:
    return isinstance(section, dict) and all(value is False for value in section.values())


def _blocked_report(reasons: list[str]) -> dict[str, Any]:
    return {
        "schema": 2,
        "decision": BLOCKED,
        "reasons": reasons,
        "claims": {
            "owner_choice_made": False,
            "scheduler_bootstrap_authorized": False,
            "production_apply_authorized": False,
        },
        "privacy": {
            "entity_ids_or_targets_emitted": False,
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "binding_hashes_emitted": False,
            "raw_yaml_emitted": False,
            "raw_storage_emitted": False,
            "private_paths_emitted": False,
            "legacy_time_values_emitted": False,
        },
        "mutation": {
            "scheduler_service_called": False,
            "helper_state_changed": False,
            "scheduler_storage_written": False,
            "home_assistant_config_written": False,
            "dashboard_written": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def precheck_reasons(
    base: dict[str, Any],
    supplemental: dict[str, Any],
    candidate: dict[str, bool],
    *,
    expected: str,
    running: str,
) -> list[str]:
    """Return public-safe RETIRE blockers before any private binding read."""
    reasons: list[str] = []

    states = _dict(base.get("states"))
    dashboard = _dict(base.get("dashboard"))
    base_privacy = _dict(base.get("privacy"))
    source = _dict(supplemental.get("source"))
    time_states = _dict(supplemental.get("states"))
    storage = _dict(supplemental.get("scheduler_storage"))
    supplemental_privacy = _dict(supplemental.get("privacy"))

    if running != expected:
        reasons.append("HOME_ASSISTANT_VERSION_MISMATCH")

    if states.get("recorder_available") is not True:
        reasons.append("RECORDER_STATE_UNAVAILABLE")

    helper_state = states.get("legacy_schedule_helper_state")
    if helper_state not in SAFE_POST_RESTART_HELPER_STATES:
        reasons.append("LEGACY_SCHEDULE_HELPER_ACTIVE_OR_UNPROVEN")

    if states.get("legacy_automation_count") != 2:
        reasons.append("LEGACY_AUTOMATIONS_NOT_EXACT")
    if states.get("legacy_automation_enabled_count") != 0:
        reasons.append("LEGACY_AUTOMATION_EXPLICITLY_ENABLED")

    if not (
        storage.get("storage_available") is True
        and storage.get("valid_wrapper") is True
        and storage.get("total_schedule_entry_count") == 0
    ):
        reasons.append("SCHEDULER_STORAGE_NOT_EMPTY_OR_INVALID")

    if dashboard.get("legacy_schedule_reference_file_count") != 0:
        reasons.append("LEGACY_DASHBOARD_REFERENCE_PRESENT")
    if dashboard.get("new_schedule_reference_file_count", 0) < 1:
        reasons.append("NEW_DASHBOARD_REFERENCE_MISSING")
    if dashboard.get("timer_reference_file_count", 0) < 1:
        reasons.append("TIMER_DASHBOARD_REFERENCE_MISSING")

    required_source_true = (
        "legacy_source_available",
        "scheduler_source_available",
        "legacy_daily_on_semantics_exact",
        "legacy_daily_off_semantics_exact",
        "legacy_gate_present",
        "timer_semantics_present",
        "scheduler_add_present",
    )
    if not all(source.get(key) is True for key in required_source_true):
        reasons.append("LEGACY_SOURCE_SEMANTICS_NOT_EXACT")

    if not candidate or not all(value is True for value in candidate.values()):
        reasons.append("CANDIDATE_SHAPE_INVALID")

    if not (_all_false(base_privacy) and _all_false(supplemental_privacy)):
        reasons.append("PRIVACY_GUARD_FAILED")

    # The latent legacy time values are intentionally *not* a RETIRE blocker.
    # They are reported below only as evidence because the transition removes
    # those helpers and #112 restart/rollback broke their Recorder continuity.
    _ = time_states

    return reasons


def _binding_reasons(binding: dict[str, Any]) -> list[str]:
    if binding.get("runtime_error") is True:
        return ["HOME_ASSISTANT_YAML_RESOLUTION_FAILED"]

    resolved_keys = (
        "legacy_on_target_resolved",
        "legacy_off_target_resolved",
        "scheduler_target_resolved",
        "timer_target_resolved",
    )
    if not all(binding.get(key) is True for key in resolved_keys):
        return ["PRIVATE_BINDING_TARGET_SHAPE_INVALID"]

    equality_keys = (
        "legacy_on_off_equal",
        "legacy_scheduler_equal",
        "timer_scheduler_equal",
        "all_four_targets_equal",
    )
    if not all(binding.get(key) is True for key in equality_keys):
        return ["PRIVATE_BINDING_VALUES_NOT_EQUAL"]

    if binding.get("installed_home_assistant_yaml_loader_used") is not True:
        return ["HOME_ASSISTANT_YAML_RESOLUTION_FAILED"]
    if binding.get("home_assistant_secret_resolution_used") is not True:
        return ["HOME_ASSISTANT_YAML_RESOLUTION_FAILED"]
    if not _all_false(binding.get("privacy")):
        return ["PRIVACY_GUARD_FAILED"]
    return []


def build_report(
    base: dict[str, Any],
    supplemental: dict[str, Any],
    candidate: dict[str, bool],
    binding: dict[str, Any],
    *,
    expected: str,
    running: str,
) -> dict[str, Any]:
    """Build the post-incident RETIRE reconciliation report."""
    reasons = precheck_reasons(
        base,
        supplemental,
        candidate,
        expected=expected,
        running=running,
    )
    if not reasons:
        reasons.extend(_binding_reasons(binding))
    if reasons:
        return _blocked_report(reasons)

    states = _dict(base.get("states"))
    source = _dict(supplemental.get("source"))
    time_states = _dict(supplemental.get("states"))
    storage = _dict(supplemental.get("scheduler_storage"))
    helper_state = states.get("legacy_schedule_helper_state")

    return {
        "schema": 2,
        "decision": READY,
        "reasons": [],
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": True,
        },
        "current_behavior": {
            "recorder_available": True,
            "recurring_schedule_active": False,
            "legacy_schedule_helper_off": helper_state == "off",
            "legacy_schedule_helper_not_on": True,
            "legacy_schedule_helper_restart_state_accepted": helper_state
            in {"unknown", "unavailable"},
            "legacy_automation_count": 2,
            "legacy_automation_enabled_count": 0,
            "legacy_automation_disabled_count": states.get(
                "legacy_automation_disabled_count", 0
            ),
            "latent_legacy_on_time_valid": time_states.get("legacy_on_time_valid")
            is True,
            "latent_legacy_off_time_valid": time_states.get("legacy_off_time_valid")
            is True,
            "latent_legacy_time_validity_required_for_retire": False,
            "scheduler_storage_empty": storage.get("total_schedule_entry_count") == 0,
        },
        "source_reconciliation": {
            "legacy_daily_on_semantics_exact": True,
            "legacy_daily_off_semantics_exact": True,
            "legacy_gate_present": True,
            "shared_binding_token_proven_without_secret_read": source.get(
                "shared_binding_token_proven_without_secret_read"
            )
            is True,
            **candidate,
            "shared_binding_value_proven_privately": True,
        },
        "binding_reconciliation": {
            "original_literal_token_proof_succeeded": source.get(
                "shared_binding_token_proven_without_secret_read"
            )
            is True,
            "resolved_value_equality_proof_succeeded": True,
            "legacy_on_off_equal": True,
            "legacy_scheduler_equal": True,
            "timer_scheduler_equal": True,
            "all_four_targets_equal": True,
        },
        "private_resolution": {
            "installed_home_assistant_yaml_loader_used": True,
            "home_assistant_secret_resolution_used": True,
            "private_binding_values_read_for_equality_only": True,
            "private_binding_values_emitted": False,
            "private_binding_hashes_emitted": False,
            "private_binding_values_persisted": False,
        },
        "transition": {
            "no_bootstrap_preserves_current_active_off_behavior": True,
            "no_bootstrap_retires_latent_legacy_time_values": True,
            "scheduler_bootstrap_requires_separate_state_preserving_authorization": True,
            "scheduler_add_entries_default_enabled": True,
            "scheduler_add_service_has_enabled_argument": False,
        },
        "claims": {
            "owner_choice_made": False,
            "scheduler_bootstrap_authorized": False,
            "production_apply_authorized": False,
        },
        "privacy": {
            "legacy_time_values_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "binding_hashes_emitted": False,
            "raw_yaml_emitted": False,
            "raw_storage_emitted": False,
            "private_paths_emitted": False,
        },
        "mutation": {
            "scheduler_service_called": False,
            "helper_state_changed": False,
            "scheduler_storage_written": False,
            "home_assistant_config_written": False,
            "dashboard_written": False,
            "heater_actuated": False,
            "reload_or_restart": False,
        },
    }


def validate_reconciliation_report(data: Any) -> bool:
    """Validate only the hardened post-incident READY shape."""
    if not isinstance(data, dict):
        return False
    if data.get("decision") != READY or data.get("reasons") != []:
        return False

    current = _dict(data.get("current_behavior"))
    binding = _dict(data.get("binding_reconciliation"))
    source = _dict(data.get("source_reconciliation"))
    transition = _dict(data.get("transition"))
    private = _dict(data.get("private_resolution"))
    ha = _dict(data.get("home_assistant"))

    required_true = (
        ha.get("version_match"),
        current.get("recorder_available"),
        current.get("legacy_schedule_helper_not_on"),
        current.get("scheduler_storage_empty"),
        binding.get("resolved_value_equality_proof_succeeded"),
        binding.get("all_four_targets_equal"),
        source.get("legacy_daily_on_semantics_exact"),
        source.get("legacy_daily_off_semantics_exact"),
        source.get("legacy_gate_present"),
        source.get("legacy_direct_automations_removed"),
        source.get("legacy_schedule_helper_removed"),
        source.get("legacy_time_helpers_removed"),
        source.get("timer_preserved"),
        source.get("scheduler_save_script_present"),
        source.get("shared_binding_value_proven_privately"),
        transition.get("no_bootstrap_preserves_current_active_off_behavior"),
        transition.get("no_bootstrap_retires_latent_legacy_time_values"),
        private.get("installed_home_assistant_yaml_loader_used"),
        private.get("home_assistant_secret_resolution_used"),
        private.get("private_binding_values_read_for_equality_only"),
    )
    if any(value is not True for value in required_true):
        return False
    if current.get("recurring_schedule_active") is not False:
        return False
    if current.get("legacy_automation_count") != 2:
        return False
    if current.get("legacy_automation_enabled_count") != 0:
        return False
    if current.get("latent_legacy_time_validity_required_for_retire") is not False:
        return False

    for section_name in ("privacy", "mutation", "claims"):
        if not _all_false(data.get(section_name)):
            return False
    return True


def collect_reconciliation(docker: str, container: str) -> dict[str, Any]:
    """Collect and validate the fresh post-incident RETIRE reconciliation."""
    expected = _planner.expected_version()
    running = _planner.running_version(docker, container)
    base = _planner.collect_runtime_probe(docker, container)
    supplemental = _planner.collect_supplemental_probe(docker, container)
    candidate = _planner.candidate_summary()

    precheck = precheck_reasons(
        base,
        supplemental,
        candidate,
        expected=expected,
        running=running,
    )
    if precheck:
        raise RuntimeError("LIVE_RECONCILIATION_BLOCKED")

    binding = _binding.collect_private_binding_probe(docker, container)
    report = build_report(
        base,
        supplemental,
        candidate,
        binding,
        expected=expected,
        running=running,
    )
    if not validate_reconciliation_report(report):
        raise RuntimeError("LIVE_RECONCILIATION_NOT_READY")
    return report
