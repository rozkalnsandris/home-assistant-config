from tools.audit_heater_scheduling_live import build_report, evaluate_report


EXPECTED = "2026.8.2"


def ready_probe() -> dict:
    return {
        "source": {
            "legacy_direct_schedule_file_count": 1,
            "scheduler_authority_file_count": 1,
            "timer_file_count": 1,
        },
        "states": {
            "recorder_available": True,
            "legacy_schedule_helper_state": "off",
            "timer_helper_state": "on",
            "legacy_automation_count": 2,
            "legacy_automation_enabled_count": 2,
            "legacy_automation_disabled_count": 0,
        },
        "scheduler": {
            "storage_available": True,
            "matching_entry_count": 2,
            "enabled_entry_count": 2,
            "turn_on_entry_count": 1,
            "turn_off_entry_count": 1,
            "other_action_entry_count": 0,
            "single_target": True,
        },
        "dashboard": {
            "new_schedule_reference_file_count": 1,
            "timer_reference_file_count": 1,
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


def test_ready_report_requires_inactive_legacy_schedule_and_scheduler_authority() -> None:
    probe = ready_probe()

    decision, reasons = evaluate_report(
        probe,
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert decision == "READY_FOR_PRIVATE_PRODUCTION_APPLY_PREPARATION"
    assert reasons == []


def test_active_legacy_schedule_helper_blocks_apply_preparation() -> None:
    probe = ready_probe()
    probe["states"]["legacy_schedule_helper_state"] = "on"

    decision, reasons = evaluate_report(
        probe,
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert decision == "BLOCKED"
    assert "LEGACY_SCHEDULE_HELPER_NOT_OFF" in reasons


def test_legacy_dashboard_reference_blocks_apply_preparation() -> None:
    probe = ready_probe()
    probe["dashboard"]["legacy_schedule_reference_file_count"] = 1

    decision, reasons = evaluate_report(
        probe,
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert decision == "BLOCKED"
    assert "LEGACY_DASHBOARD_REFERENCE_STILL_PRESENT" in reasons


def test_missing_scheduler_entries_requires_review() -> None:
    probe = ready_probe()
    probe["scheduler"]["matching_entry_count"] = 0
    probe["scheduler"]["enabled_entry_count"] = 0
    probe["scheduler"]["single_target"] = False

    decision, reasons = evaluate_report(
        probe,
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert decision == "NEEDS_REVIEW"
    assert "NO_MATCHING_SCHEDULER_ENTRIES" in reasons
    assert "NO_ENABLED_SCHEDULER_ENTRIES" in reasons
    assert "SCHEDULER_TARGET_NOT_SINGLE" in reasons


def test_version_mismatch_blocks() -> None:
    decision, reasons = evaluate_report(
        ready_probe(),
        expected=EXPECTED,
        running="2026.8.1",
    )

    assert decision == "BLOCKED"
    assert "HOME_ASSISTANT_VERSION_MISMATCH" in reasons


def test_public_report_contains_only_sanitized_runtime_evidence() -> None:
    report = build_report(
        ready_probe(),
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["privacy"]["exact_entity_targets_emitted"] is False
    assert report["privacy"]["schedule_times_emitted"] is False
    assert report["privacy"]["weekdays_emitted"] is False
    assert report["privacy"]["dashboard_paths_emitted"] is False
    assert report["privacy"]["config_contents_emitted"] is False
    assert report["privacy"]["secrets_read"] is False
    assert report["mutation"] == {
        "home_assistant_write": False,
        "scheduler_write": False,
        "dashboard_write": False,
        "reload_or_restart": False,
    }
