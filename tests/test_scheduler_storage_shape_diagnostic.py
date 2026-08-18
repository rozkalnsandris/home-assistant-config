import json

from tools.diagnose_scheduler_storage_shape import (
    BLOCKED_DECISION,
    EMPTY_DECISION,
    PRESENT_DECISION,
    build_report,
    main,
    summarize_entries,
)


EXPECTED = "2026.8.2"


def entries_fixture() -> list[dict]:
    return [
        {
            "name": "private schedule alpha",
            "enabled": True,
            "timeslots": [
                {
                    "start": "06:00",
                    "actions": [
                        {
                            "service": "switch.turn_on",
                            "entity_id": "switch.private_target_a",
                        }
                    ],
                }
            ],
        },
        {
            "name": "private schedule beta",
            "enabled": True,
            "timeslots": [
                {
                    "start": "08:00",
                    "actions": [
                        {
                            "service": "switch.turn_off",
                            "entity_id": "switch.private_target_a",
                        }
                    ],
                }
            ],
        },
        {
            "name": "private schedule gamma",
            "enabled": False,
            "timeslots": [
                {
                    "start": "09:00",
                    "actions": [
                        {
                            "service": "light.turn_on",
                            "entity_id": "light.private_target_b",
                        }
                    ],
                }
            ],
        },
    ]


def test_summary_is_name_independent_and_aggregate_only() -> None:
    summary = summarize_entries(entries_fixture())

    assert summary == {
        "total_schedule_entry_count": 3,
        "enabled_entry_count": 2,
        "zero_action_target_entry_count": 0,
        "single_action_target_entry_count": 3,
        "multiple_action_target_entry_count": 0,
        "turn_on_only_entry_count": 1,
        "turn_off_only_entry_count": 1,
        "mixed_or_other_action_entry_count": 1,
        "malformed_or_unsupported_entry_count": 0,
        "distinct_private_target_count": 2,
        "private_targets_shared_by_multiple_schedules_count": 1,
        "schedules_on_shared_private_targets_count": 2,
        "max_schedule_count_for_single_private_target": 2,
        "private_target_with_on_and_off_schedule_pair_present": True,
    }


def test_entries_present_requires_private_target_correlation() -> None:
    report = build_report(
        entries=entries_fixture(),
        storage_bytes=1234,
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == PRESENT_DECISION
    assert report["reasons"] == []
    assert report["claims"] == {
        "heater_target_identified": False,
        "scheduler_authority_proven": False,
        "production_apply_authorized": False,
    }


def test_empty_storage_is_distinct_from_name_match_failure() -> None:
    report = build_report(
        entries=[],
        storage_bytes=120,
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == EMPTY_DECISION
    assert report["scheduler_storage"]["total_schedule_entry_count"] == 0


def test_unsupported_entry_shape_blocks() -> None:
    entries = [{"enabled": True, "timeslots": "not-a-list"}]

    report = build_report(
        entries=entries,
        storage_bytes=200,
        expected=EXPECTED,
        running=EXPECTED,
    )

    assert report["decision"] == BLOCKED_DECISION
    assert report["reasons"] == ["SCHEDULER_ENTRY_SHAPE_UNSUPPORTED"]


def test_version_mismatch_blocks() -> None:
    report = build_report(
        entries=entries_fixture(),
        storage_bytes=1234,
        expected=EXPECTED,
        running="2026.8.1",
    )

    assert report["decision"] == BLOCKED_DECISION
    assert report["reasons"] == ["HOME_ASSISTANT_VERSION_MISMATCH"]


def test_public_report_does_not_emit_private_schedule_payload() -> None:
    report = build_report(
        entries=entries_fixture(),
        storage_bytes=1234,
        expected=EXPECTED,
        running=EXPECTED,
    )
    encoded = json.dumps(report, sort_keys=True)

    assert "private schedule" not in encoded
    assert "switch.private_target_a" not in encoded
    assert "light.private_target_b" not in encoded
    assert "06:00" not in encoded
    assert report["privacy"] == {
        "schedule_names_emitted": False,
        "schedule_ids_emitted": False,
        "entity_ids_or_targets_emitted": False,
        "schedule_times_emitted": False,
        "weekdays_or_dates_emitted": False,
        "service_data_emitted": False,
        "storage_paths_or_keys_emitted": False,
        "raw_storage_json_emitted": False,
        "dashboard_content_emitted": False,
        "secrets_read": False,
    }
    assert all(value is False for value in report["mutation"].values())


def test_cli_without_explicit_gate_is_fail_closed(capsys) -> None:
    rc = main([])
    captured = json.loads(capsys.readouterr().out)

    assert rc == 20
    assert captured["decision"] == BLOCKED_DECISION
    assert captured["reasons"] == ["DIAGNOSTIC_GATE_REQUIRED"]
