import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import tools.heater_retire_production_common as common
import tools.heater_retire_production_preflight as preflight
import tools.run_heater_retire_production_gate as gate


DUMMY_SHA = "a" * 40
DUMMY_TREE = "b" * 40
DUMMY_PARENT = "c" * 40
EXPECTED_VERSION = "2026.8.2"


def dry_run_pass() -> dict:
    return {
        "decision": preflight.DRY_RUN_READY,
        "home_assistant": {
            "expected_version_match": True,
            "running_version_match": True,
            "runtime_unchanged": True,
        },
        "live_preconditions_ready": True,
        "owner_semantic_choice": "RETIRE",
        "privacy": {"fixture": False},
        "private_temp_cleanup": True,
        "production_apply_authorized": False,
        "production_mutation": {"fixture": False},
        "scheduler_bootstrap_authorized": False,
        "source_gate": {
            "parent_match": True,
            "sha_match": True,
            "tree_match": True,
        },
        "worker": {
            "decision": preflight.DRY_RUN_WORKER_READY,
            "full_temp_check_config_passed": True,
            "hardened_apply": {
                "results": [
                    {
                        "classification": "equals_candidate",
                        "ordinal": 1,
                        "parent_fsync_performed": True,
                        "phase": "apply",
                    },
                    {
                        "classification": "equals_candidate",
                        "ordinal": 2,
                        "parent_fsync_performed": True,
                        "phase": "apply",
                    },
                ],
                "verified_count": 2,
            },
            "hardened_rollback": {
                "results": [
                    {
                        "classification": "equals_original",
                        "ordinal": 1,
                        "parent_fsync_performed": True,
                        "phase": "rollback",
                    },
                    {
                        "classification": "equals_original",
                        "ordinal": 2,
                        "parent_fsync_performed": True,
                        "phase": "rollback",
                    },
                ],
                "temp_originals_restored": True,
                "verified_count": 2,
            },
            "live_invariants": {
                "heater_files_unchanged": True,
                "scheduler_storage_empty_after": True,
                "scheduler_storage_unchanged": True,
            },
            "privacy": {"fixture": False},
            "production_mutation": {"fixture": False},
        },
    }


def scheduler_payload() -> bytes:
    return (
        json.dumps(
            {"version": 1, "data": {"schedules": []}},
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def test_expected_authorization_is_exact_and_source_bound() -> None:
    expected = common.expected_authorization(DUMMY_SHA)
    assert expected == (
        "Authorize heater RETIRE production apply "
        + DUMMY_SHA
        + "-heater-retire-123-v1"
    )


def test_direct_cli_is_import_safe_and_fails_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "tools" / "run_heater_retire_production_gate.py"),
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 20
    report = json.loads(result.stdout)
    assert report["decision"] == gate.BLOCKED
    assert report["reason"] == "EXPLICIT_PRODUCTION_EXECUTION_GATE_REQUIRED"
    assert "ModuleNotFoundError" not in result.stderr


def test_main_without_execute_gate_fails_closed(capsys) -> None:
    rc = gate.main([])
    report = json.loads(capsys.readouterr().out)
    assert rc == 20
    assert report["decision"] == gate.BLOCKED
    assert report["reason"] == "EXPLICIT_PRODUCTION_EXECUTION_GATE_REQUIRED"
    assert report["authorization_consumed"] is False
    assert report["production_change"] is False
    assert all(value is False for value in report["production_mutation"].values())


def test_main_missing_required_gate_arguments_fails_closed(capsys) -> None:
    rc = gate.main(["--execute", "--retire"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 20
    assert report["decision"] == gate.BLOCKED
    assert report["reason"] == "PRODUCTION_GATE_ARGUMENTS_REQUIRED"
    assert report["production_change"] is False


def test_authorization_marker_is_one_shot(tmp_path: Path) -> None:
    marker_base = tmp_path / "auth"
    authorization = common.expected_authorization(DUMMY_SHA)
    common.authorization_precheck(
        marker_base=marker_base,
        expected_sha=DUMMY_SHA,
        authorization=authorization,
    )
    common.consume_authorization(
        marker_base=marker_base,
        expected_sha=DUMMY_SHA,
        authorization=authorization,
    )
    with pytest.raises(common.GateError, match="AUTHORIZATION_ALREADY_CONSUMED"):
        common.authorization_precheck(
            marker_base=marker_base,
            expected_sha=DUMMY_SHA,
            authorization=authorization,
        )


def test_wrong_authorization_does_not_create_marker(tmp_path: Path) -> None:
    marker_base = tmp_path / "auth"
    with pytest.raises(
        common.GateError,
        match="EXACT_ONE_SHOT_AUTHORIZATION_REQUIRED",
    ):
        common.consume_authorization(
            marker_base=marker_base,
            expected_sha=DUMMY_SHA,
            authorization="not-authorized",
        )
    assert not marker_base.exists()


def test_discover_config_root_uses_unique_config_mount_without_public_output(
    tmp_path: Path,
) -> None:
    config_root = tmp_path / "private-config"
    config_root.mkdir()
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            [
                {
                    "Mounts": [
                        {
                            "Destination": "/config",
                            "Source": str(config_root),
                        }
                    ]
                }
            ]
        ),
        stderr="",
    )
    with patch.object(common, "run", return_value=completed):
        discovered = common.discover_config_root("docker", "homeassistant")
    assert discovered == config_root
    encoded = json.dumps(gate.blocked_report("FIXTURE"))
    assert str(config_root) not in encoded


def test_validate_exact_hardened_dry_run_pass_shape() -> None:
    report = dry_run_pass()
    assert preflight.validate_dry_run_report(report)
    report["worker"]["hardened_apply"]["results"][0][
        "parent_fsync_performed"
    ] = False
    assert not preflight.validate_dry_run_report(report)


def test_runtime_safety_accepts_observed_postincident_shape() -> None:
    report = common.runtime_safety(
        {
            "recorder_available": True,
            "legacy_schedule_helper_state": "unavailable",
            "legacy_automation_count": 2,
            "legacy_automation_enabled_count": 0,
            "legacy_automation_disabled_count": 0,
            "timer_helper_state": "off",
        },
        expected_timer_state="off",
        require_legacy_count=True,
    )
    assert report["legacy_schedule_helper_not_on"] is True
    assert report["legacy_automation_enabled_count"] == 0
    assert report["timer_helper_state_matches_prewrite"] is True
    assert report["full_state_machine_equivalence_claimed"] is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        (
            "legacy_schedule_helper_state",
            "on",
            "LEGACY_SCHEDULE_HELPER_EXPLICITLY_ON_OR_INVALID",
        ),
        (
            "legacy_automation_enabled_count",
            1,
            "LEGACY_AUTOMATION_EXPLICITLY_ENABLED",
        ),
        (
            "timer_helper_state",
            "unknown",
            "TIMER_HELPER_STATE_NOT_OBSERVABLE",
        ),
    ],
)
def test_runtime_safety_hard_blockers(
    field: str,
    value: object,
    reason: str,
) -> None:
    states = {
        "recorder_available": True,
        "legacy_schedule_helper_state": "unavailable",
        "legacy_automation_count": 2,
        "legacy_automation_enabled_count": 0,
        "legacy_automation_disabled_count": 0,
        "timer_helper_state": "off",
    }
    states[field] = value
    with pytest.raises(common.GateError, match=reason):
        common.runtime_safety(
            states,
            expected_timer_state="off",
            require_legacy_count=True,
        )


def test_runtime_safety_blocks_timer_state_drift() -> None:
    with pytest.raises(common.GateError, match="TIMER_HELPER_STATE_DRIFTED"):
        common.runtime_safety(
            {
                "recorder_available": True,
                "legacy_schedule_helper_state": "off",
                "legacy_automation_count": 2,
                "legacy_automation_enabled_count": 0,
                "timer_helper_state": "on",
            },
            expected_timer_state="off",
            require_legacy_count=True,
        )


def test_scheduler_raw_identity_required_before_restart(tmp_path: Path) -> None:
    before = tmp_path / "before"
    current = tmp_path / "current"
    before.write_bytes(scheduler_payload())
    current.write_text(
        '{\n  "data": {"schedules": []}, "version": 1\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(
        common.GateError,
        match="SCHEDULER_STRICT_PRE_RESTART_INVARIANT_FAILED",
    ):
        preflight.strict_scheduler_invariant(before, current)


def test_scheduler_empty_semantics_allow_reserialization_after_restart(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before"
    current = tmp_path / "current"
    before.write_bytes(scheduler_payload())
    current.write_text(
        '{\n  "data": {"schedules": []}, "version": 1\n}\n',
        encoding="utf-8",
    )
    with patch.object(preflight.time, "sleep", return_value=None):
        report = preflight.stable_scheduler_semantic_invariant(before, current)
    assert report["decision"] == "SCHEDULER_SEMANTIC_INVARIANT_PASS"
    assert report["scheduler"]["before_schedule_count"] == 0
    assert report["scheduler"]["current_schedule_count"] == 0
    assert report["scheduler"]["bytes_equal"] is False
    assert report["scheduler"]["raw_byte_identity_required_for_pass"] is False


def _prepare_execute_fixture(tmp_path: Path):
    config_root = tmp_path / "config"
    packages = config_root / "packages"
    storage = config_root / ".storage"
    packages.mkdir(parents=True)
    storage.mkdir()
    live_one = packages / "silditajs.yaml"
    live_two = packages / "heater_scheduler.yaml"
    live_one.write_bytes(b"original-one\n")
    live_two.write_bytes(b"original-two\n")
    (storage / "scheduler.storage").write_bytes(scheduler_payload())

    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate_one = candidates / "one"
    candidate_two = candidates / "two"
    candidate_one.write_bytes(b"candidate-one\n")
    candidate_two.write_bytes(b"candidate-two\n")

    args = SimpleNamespace(
        docker="docker",
        container="homeassistant",
        expected_sha=DUMMY_SHA,
        expected_tree=DUMMY_TREE,
        expected_parent=DUMMY_PARENT,
        expected_version=EXPECTED_VERSION,
        authorization=common.expected_authorization(DUMMY_SHA),
    )
    return config_root, live_one, live_two, candidate_one, candidate_two, args


def _fake_runtime(*, expected_timer_state, require_legacy_count, **_kwargs):
    return (
        {
            "recorder_available": True,
            "legacy_schedule_helper_not_on": True,
            "legacy_automation_enabled_count": 0,
            "legacy_automation_count": 2 if require_legacy_count else 0,
            "timer_helper_state_observable": True,
            "timer_helper_state_matches_prewrite": True,
            "full_state_machine_equivalence_claimed": False,
        },
        "off",
    )


def test_restart_failure_forces_rollback_restart_path(
    tmp_path: Path,
) -> None:
    (
        config_root,
        live_one,
        live_two,
        candidate_one,
        candidate_two,
        args,
    ) = _prepare_execute_fixture(tmp_path)

    restart_sequence = [
        common.GateError("HOME_ASSISTANT_RESTART_FAILED"),
        {
            "restart_boundary_changed": True,
            "running_version_match": True,
            "http_listener_ready": True,
        },
    ]
    with (
        patch.object(common, "AUTH_MARKER_BASE", tmp_path / "auth"),
        patch.object(common, "ROLLBACK_BASE", tmp_path / "rollback"),
        patch.object(
            common,
            "source_gate",
            return_value={
                "sha_match": True,
                "tree_match": True,
                "parent_match": True,
            },
        ),
        patch.object(
            common,
            "expected_version_from_source",
            return_value=EXPECTED_VERSION,
        ),
        patch.object(common, "running_version", return_value=EXPECTED_VERSION),
        patch.object(common, "collect_runtime_safety", side_effect=_fake_runtime),
        patch.object(common, "discover_config_root", return_value=config_root),
        patch.object(common, "check_live_config", return_value=True),
        patch.object(common, "container_started_at", return_value="before"),
        patch.object(
            common,
            "restart_and_wait",
            side_effect=restart_sequence,
        ) as restart,
        patch.object(preflight, "run_fresh_dry_run", return_value=dry_run_pass()),
        patch.object(
            preflight,
            "materialize_candidates",
            return_value=(
                candidate_one,
                candidate_two,
                {"decision": "PRIVATE_RETIRE_CANDIDATE_MATERIALIZED"},
            ),
        ),
        patch.object(preflight.time, "sleep", return_value=None),
    ):
        report = gate.execute_gate(args)

    assert restart.call_count == 2
    assert live_one.read_bytes() == b"original-one\n"
    assert live_two.read_bytes() == b"original-two\n"
    assert report["decision"] == gate.APPLY_FAILED_ROLLBACK_COMPLETE
    assert report["reason"] == "HOME_ASSISTANT_RESTART_FAILED"
    assert report["production_change"] is True
    assert report["production_mutation"]["restart_attempted"] is True
    assert report["current_candidate_active"] is False


def test_unexpected_postwrite_error_still_rolls_back(
    tmp_path: Path,
) -> None:
    (
        config_root,
        live_one,
        live_two,
        candidate_one,
        candidate_two,
        args,
    ) = _prepare_execute_fixture(tmp_path)

    check_calls = 0

    def fake_check(*_args):
        nonlocal check_calls
        check_calls += 1
        if check_calls == 2:
            raise OSError("fixture")
        return True

    with (
        patch.object(common, "AUTH_MARKER_BASE", tmp_path / "auth"),
        patch.object(common, "ROLLBACK_BASE", tmp_path / "rollback"),
        patch.object(
            common,
            "source_gate",
            return_value={
                "sha_match": True,
                "tree_match": True,
                "parent_match": True,
            },
        ),
        patch.object(
            common,
            "expected_version_from_source",
            return_value=EXPECTED_VERSION,
        ),
        patch.object(common, "running_version", return_value=EXPECTED_VERSION),
        patch.object(common, "collect_runtime_safety", side_effect=_fake_runtime),
        patch.object(common, "discover_config_root", return_value=config_root),
        patch.object(common, "check_live_config", side_effect=fake_check),
        patch.object(preflight, "run_fresh_dry_run", return_value=dry_run_pass()),
        patch.object(
            preflight,
            "materialize_candidates",
            return_value=(
                candidate_one,
                candidate_two,
                {"decision": "PRIVATE_RETIRE_CANDIDATE_MATERIALIZED"},
            ),
        ),
    ):
        report = gate.execute_gate(args)

    assert live_one.read_bytes() == b"original-one\n"
    assert live_two.read_bytes() == b"original-two\n"
    assert report["decision"] == gate.APPLY_FAILED_ROLLBACK_COMPLETE
    assert report["reason"] == "PRODUCTION_GATE_UNEXPECTED_ERROR"
    assert report["production_change"] is True
    assert report["current_candidate_active"] is False


def test_public_blocked_report_never_contains_private_details() -> None:
    encoded = json.dumps(gate.blocked_report("FIXTURE"), sort_keys=True)
    assert "/config" not in encoded
    assert "switch." not in encoded
    assert "!secret" not in encoded
    assert "sha256" not in encoded
