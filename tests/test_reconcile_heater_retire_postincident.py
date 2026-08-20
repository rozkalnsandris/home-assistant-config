import json

from tools.reconcile_heater_retire_postincident import (
    BLOCKED,
    READY,
    build_report,
    precheck_reasons,
    validate_reconciliation_report,
)

EXPECTED = "2026.8.2"


def base_probe(*, helper_state: str = "unavailable", enabled_count: int = 0) -> dict:
    return {
        "states": {
            "recorder_available": True,
            "legacy_schedule_helper_state": helper_state,
            "timer_helper_state": "off",
            "legacy_automation_count": 2,
            "legacy_automation_enabled_count": enabled_count,
            "legacy_automation_disabled_count": 0,
        },
        "dashboard": {
            "new_schedule_reference_file_count": 2,
            "timer_reference_file_count": 2,
            "legacy_schedule_reference_file_count": 0,
        },
        "privacy": {
            "exact_entity_targets_emitted": False,
            "scheduler_names_emitted": False,
            "schedule_times_emitted": False,
            "weekdays_emitted": False,
            "dashboard_paths_emitted": False,
            "config_contents_emitted": False,
            "secrets_read": False,
        },
    }


def supplemental_probe(*, schedules: int = 0) -> dict:
    return {
        "source": {
            "legacy_source_available": True,
            "scheduler_source_available": True,
            "legacy_daily_on_semantics_exact": True,
            "legacy_daily_off_semantics_exact": True,
            "legacy_gate_present": True,
            "timer_semantics_present": True,
            "scheduler_add_present": True,
            "shared_binding_token_proven_without_secret_read": False,
        },
        "states": {
            "recorder_available": True,
            "legacy_on_time_valid": False,
            "legacy_off_time_valid": False,
        },
        "scheduler_storage": {
            "storage_available": True,
            "valid_wrapper": True,
            "total_schedule_entry_count": schedules,
        },
        "privacy": {
            "legacy_time_values_emitted": False,
            "entity_ids_or_targets_emitted": False,
            "secret_values_emitted": False,
            "raw_yaml_emitted": False,
            "raw_storage_emitted": False,
            "private_paths_emitted": False,
        },
    }


def candidate() -> dict[str, bool]:
    return {
        "legacy_schedule_helper_removed": True,
        "legacy_time_helpers_removed": True,
        "legacy_direct_automations_removed": True,
        "timer_preserved": True,
        "scheduler_save_script_present": True,
        "candidate_binding_token_present": True,
    }


def binding_probe(*, equal: bool = True) -> dict:
    return {
        "installed_home_assistant_yaml_loader_used": True,
        "home_assistant_secret_resolution_used": True,
        "runtime_error": False,
        "legacy_on_target_resolved": True,
        "legacy_off_target_resolved": True,
        "scheduler_target_resolved": True,
        "timer_target_resolved": True,
        "legacy_on_off_equal": equal,
        "legacy_scheduler_equal": equal,
        "timer_scheduler_equal": equal,
        "all_four_targets_equal": equal,
        "privacy": {
            "entity_ids_or_targets_emitted": False,
            "secret_aliases_emitted": False,
            "secret_values_emitted": False,
            "binding_hashes_emitted": False,
            "raw_yaml_emitted": False,
            "private_paths_emitted": False,
        },
    }


def ready_report() -> dict:
    return build_report(
        base_probe(),
        supplemental_probe(),
        candidate(),
        binding_probe(),
        expected=EXPECTED,
        running=EXPECTED,
    )


def test_current_postincident_snapshot_shape_is_ready_for_retire() -> None:
    report = ready_report()

    assert report["decision"] == READY
    assert report["reasons"] == []
    assert report["current_behavior"]["legacy_schedule_helper_not_on"] is True
    assert report["current_behavior"][
        "legacy_schedule_helper_restart_state_accepted"
    ] is True
    assert report["current_behavior"]["legacy_automation_enabled_count"] == 0
    assert report["current_behavior"][
        "latent_legacy_time_validity_required_for_retire"
    ] is False
    assert report["current_behavior"]["latent_legacy_on_time_valid"] is False
    assert report["current_behavior"]["latent_legacy_off_time_valid"] is False
    assert validate_reconciliation_report(report)


def test_explicit_helper_on_hard_blocks() -> None:
    reasons = precheck_reasons(
        base_probe(helper_state="on"),
        supplemental_probe(),
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )
    assert "LEGACY_SCHEDULE_HELPER_ACTIVE_OR_UNPROVEN" in reasons


def test_any_explicit_enabled_legacy_automation_hard_blocks() -> None:
    reasons = precheck_reasons(
        base_probe(enabled_count=1),
        supplemental_probe(),
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )
    assert "LEGACY_AUTOMATION_EXPLICITLY_ENABLED" in reasons


def test_off_unknown_and_unavailable_are_accepted_restart_states() -> None:
    for state in ("off", "unknown", "unavailable"):
        reasons = precheck_reasons(
            base_probe(helper_state=state),
            supplemental_probe(),
            candidate(),
            expected=EXPECTED,
            running=EXPECTED,
        )
        assert "LEGACY_SCHEDULE_HELPER_ACTIVE_OR_UNPROVEN" not in reasons


def test_nonempty_scheduler_still_hard_blocks() -> None:
    report = build_report(
        base_probe(),
        supplemental_probe(schedules=1),
        candidate(),
        binding_probe(),
        expected=EXPECTED,
        running=EXPECTED,
    )
    assert report["decision"] == BLOCKED
    assert report["reasons"] == ["SCHEDULER_STORAGE_NOT_EMPTY_OR_INVALID"]


def test_private_binding_mismatch_still_hard_blocks() -> None:
    report = build_report(
        base_probe(),
        supplemental_probe(),
        candidate(),
        binding_probe(equal=False),
        expected=EXPECTED,
        running=EXPECTED,
    )
    assert report["decision"] == BLOCKED
    assert report["reasons"] == ["PRIVATE_BINDING_VALUES_NOT_EQUAL"]


def test_legacy_gate_semantics_remain_mandatory() -> None:
    supplemental = supplemental_probe()
    supplemental["source"]["legacy_gate_present"] = False
    report = build_report(
        base_probe(),
        supplemental,
        candidate(),
        binding_probe(),
        expected=EXPECTED,
        running=EXPECTED,
    )
    assert report["decision"] == BLOCKED
    assert "LEGACY_SOURCE_SEMANTICS_NOT_EXACT" in report["reasons"]


def test_report_remains_privacy_safe() -> None:
    report = ready_report()
    encoded = json.dumps(report, sort_keys=True)

    assert "switch." not in encoded
    assert "heater_switch_entity" not in encoded
    assert "/config" not in encoded
    assert all(value is False for value in report["privacy"].values())
    assert all(value is False for value in report["mutation"].values())
    assert all(value is False for value in report["claims"].values())
