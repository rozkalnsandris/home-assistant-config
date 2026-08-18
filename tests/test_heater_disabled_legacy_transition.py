import json

from tools.plan_heater_disabled_legacy_transition import (
    BLOCKED,
    READY,
    blocked_report,
    build_report,
    candidate_summary,
    main,
)

EXPECTED = "2026.8.2"


def base_probe() -> dict:
    return {
        "states": {
            "legacy_schedule_helper_state": "off",
            "legacy_automation_count": 2,
            "legacy_automation_enabled_count": 2,
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


def supplemental_probe() -> dict:
    return {
        "source": {
            "legacy_source_available": True,
            "scheduler_source_available": True,
            "legacy_daily_on_semantics_exact": True,
            "legacy_daily_off_semantics_exact": True,
            "legacy_gate_present": True,
            "timer_semantics_present": True,
            "scheduler_add_present": True,
            "shared_binding_token_proven_without_secret_read": True,
        },
        "states": {
            "recorder_available": True,
            "legacy_on_time_valid": True,
            "legacy_off_time_valid": True,
        },
        "scheduler_storage": {
            "storage_available": True,
            "valid_wrapper": True,
            "total_schedule_entry_count": 0,
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


def candidate() -> dict:
    return {
        "legacy_schedule_helper_removed": True,
        "legacy_time_helpers_removed": True,
        "legacy_direct_automations_removed": True,
        "timer_preserved": True,
        "scheduler_save_script_present": True,
        "candidate_binding_token_present": True,
    }


def test_ready_requires_disabled_legacy_and_empty_scheduler() -> None:
    report = build_report(
        base_probe(),
        supplemental_probe(),
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == READY
    assert report["reasons"] == []
    assert report["current_behavior"]["recurring_schedule_active"] is False
    assert report["transition"] == {
        "no_bootstrap_preserves_current_active_off_behavior": True,
        "no_bootstrap_retires_latent_legacy_time_values": True,
        "scheduler_bootstrap_requires_separate_state_preserving_authorization": True,
        "scheduler_add_entries_default_enabled": True,
        "scheduler_add_service_has_enabled_argument": False,
    }


def test_active_legacy_schedule_blocks() -> None:
    base = base_probe()
    base["states"]["legacy_schedule_helper_state"] = "on"
    report = build_report(
        base,
        supplemental_probe(),
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == BLOCKED
    assert "LEGACY_SCHEDULE_HELPER_NOT_OFF" in report["reasons"]


def test_nonempty_scheduler_blocks_this_transition_shape() -> None:
    supplemental = supplemental_probe()
    supplemental["scheduler_storage"]["total_schedule_entry_count"] = 1
    report = build_report(
        base_probe(),
        supplemental,
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == BLOCKED
    assert "SCHEDULER_STORAGE_NOT_EMPTY" in report["reasons"]


def test_invalid_latent_time_blocks() -> None:
    supplemental = supplemental_probe()
    supplemental["states"]["legacy_on_time_valid"] = False
    report = build_report(
        base_probe(),
        supplemental,
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == BLOCKED
    assert "LEGACY_ON_TIME_INVALID" in report["reasons"]


def test_legacy_dashboard_reference_blocks() -> None:
    base = base_probe()
    base["dashboard"]["legacy_schedule_reference_file_count"] = 1
    report = build_report(
        base,
        supplemental_probe(),
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == BLOCKED
    assert "LEGACY_DASHBOARD_REFERENCE_PRESENT" in report["reasons"]


def test_version_mismatch_blocks() -> None:
    report = build_report(
        base_probe(),
        supplemental_probe(),
        candidate(),
        expected=EXPECTED,
        running="2026.8.1",
    )

    assert report["decision"] == BLOCKED
    assert "HOME_ASSISTANT_VERSION_MISMATCH" in report["reasons"]


def test_public_report_never_contains_private_values() -> None:
    report = build_report(
        base_probe(),
        supplemental_probe(),
        candidate(),
        expected=EXPECTED,
        running=EXPECTED,
    )
    encoded = json.dumps(report, sort_keys=True)

    assert "06:00" not in encoded
    assert "22:00" not in encoded
    assert "switch.private" not in encoded
    assert all(value is False for value in report["privacy"].values())
    assert all(value is False for value in report["mutation"].values())
    assert report["claims"] == {
        "owner_choice_made": False,
        "scheduler_bootstrap_authorized": False,
        "production_apply_authorized": False,
    }


def test_candidate_summary_matches_current_reviewed_source() -> None:
    summary = candidate_summary()
    assert all(summary.values())


def test_cli_without_plan_is_fail_closed(capsys) -> None:
    rc = main([])
    report = json.loads(capsys.readouterr().out)

    assert rc == 20
    assert report == blocked_report("PLANNER_GATE_REQUIRED")
