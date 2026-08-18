import json

from tools.reconcile_heater_binding_privately import (
    BLOCKED,
    READY,
    blocked_report,
    main,
    reconcile_report,
)


def original_binding_only_blocked() -> dict:
    return {
        "schema": 1,
        "decision": BLOCKED,
        "reasons": ["LEGACY_SOURCE_SEMANTICS_NOT_EXACT"],
        "home_assistant": {
            "expected_version": "2026.8.2",
            "running_version": "2026.8.2",
            "version_match": True,
        },
        "current_behavior": {
            "recurring_schedule_active": False,
            "legacy_schedule_helper_off": True,
            "legacy_automation_count": 2,
            "legacy_automation_enabled_count": 2,
            "latent_legacy_on_time_valid": True,
            "latent_legacy_off_time_valid": True,
            "scheduler_storage_empty": True,
        },
        "source_reconciliation": {
            "legacy_daily_on_semantics_exact": True,
            "legacy_daily_off_semantics_exact": True,
            "shared_binding_token_proven_without_secret_read": False,
            "legacy_schedule_helper_removed": True,
            "legacy_time_helpers_removed": True,
            "legacy_direct_automations_removed": True,
            "timer_preserved": True,
            "scheduler_save_script_present": True,
            "candidate_binding_token_present": True,
        },
        "transition": {
            "no_bootstrap_preserves_current_active_off_behavior": False,
            "no_bootstrap_retires_latent_legacy_time_values": False,
            "scheduler_bootstrap_requires_separate_state_preserving_authorization": False,
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
            "secret_values_emitted": False,
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


def test_private_binding_proof_reconciles_binding_only_blocker() -> None:
    report = reconcile_report(original_binding_only_blocked(), binding_probe())

    assert report["decision"] == READY
    assert report["reasons"] == []
    assert report["source_reconciliation"][
        "shared_binding_token_proven_without_secret_read"
    ] is False
    assert report["source_reconciliation"][
        "shared_binding_value_proven_privately"
    ] is True
    assert report["binding_reconciliation"] == {
        "original_literal_token_proof_succeeded": False,
        "resolved_value_equality_proof_succeeded": True,
        "legacy_on_off_equal": True,
        "legacy_scheduler_equal": True,
        "timer_scheduler_equal": True,
        "all_four_targets_equal": True,
    }
    assert report["transition"] == {
        "no_bootstrap_preserves_current_active_off_behavior": True,
        "no_bootstrap_retires_latent_legacy_time_values": True,
        "scheduler_bootstrap_requires_separate_state_preserving_authorization": True,
        "scheduler_add_entries_default_enabled": True,
        "scheduler_add_service_has_enabled_argument": False,
    }
    assert all(value is False for value in report["claims"].values())
    assert all(value is False for value in report["mutation"].values())


def test_private_binding_mismatch_blocks() -> None:
    report = reconcile_report(
        original_binding_only_blocked(), binding_probe(equal=False)
    )

    assert report["decision"] == BLOCKED
    assert report["reasons"] == ["PRIVATE_BINDING_VALUES_NOT_EQUAL"]


def test_unresolved_target_shape_blocks() -> None:
    probe = binding_probe()
    probe["scheduler_target_resolved"] = False

    report = reconcile_report(original_binding_only_blocked(), probe)

    assert report["decision"] == BLOCKED
    assert report["reasons"] == ["PRIVATE_BINDING_TARGET_SHAPE_INVALID"]


def test_yaml_resolution_failure_blocks() -> None:
    probe = binding_probe()
    probe["runtime_error"] = True

    report = reconcile_report(original_binding_only_blocked(), probe)

    assert report["decision"] == BLOCKED
    assert report["reasons"] == ["HOME_ASSISTANT_YAML_RESOLUTION_FAILED"]


def test_extra_original_blocker_cannot_be_overridden() -> None:
    original = original_binding_only_blocked()
    original["reasons"] = [
        "LEGACY_SOURCE_SEMANTICS_NOT_EXACT",
        "SCHEDULER_STORAGE_NOT_EMPTY",
    ]

    report = reconcile_report(original, binding_probe())

    assert report["decision"] == BLOCKED
    assert report["reasons"] == ["ORIGINAL_PLANNER_NOT_BINDING_ONLY_BLOCKED"]


def test_public_ready_report_never_contains_private_binding_values() -> None:
    report = reconcile_report(original_binding_only_blocked(), binding_probe())
    encoded = json.dumps(report, sort_keys=True)

    assert "switch.private_heater" not in encoded
    assert "heater_switch_entity" not in encoded
    assert "secret-value" not in encoded
    assert "sha256" not in encoded
    assert all(value is False for value in report["privacy"].values())
    assert report["private_resolution"] == {
        "installed_home_assistant_yaml_loader_used": True,
        "home_assistant_secret_resolution_used": True,
        "private_binding_values_read_for_equality_only": True,
        "private_binding_values_emitted": False,
        "private_binding_hashes_emitted": False,
        "private_binding_values_persisted": False,
    }


def test_cli_without_reconcile_is_fail_closed(capsys) -> None:
    rc = main([])
    report = json.loads(capsys.readouterr().out)

    assert rc == 20
    assert report == blocked_report("RECONCILIATION_GATE_REQUIRED")
