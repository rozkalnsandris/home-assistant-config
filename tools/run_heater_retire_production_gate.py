#!/usr/bin/env python3
"""One-shot production gate for the reviewed heater RETIRE transition.

This module does not grant authorization. It requires an exact source-bound
owner phrase and permanently consumes that phrase at the first live-write
boundary.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.heater_retire_production_common as common
import tools.heater_retire_production_preflight as preflight
from tools.run_heater_retire_hardened_dry_run_impl import (
    LIVE_ONE_RELATIVE,
    LIVE_TWO_RELATIVE,
    SCHEDULER_STORAGE_RELATIVE,
)
from tools.verify_bounded_private_file_replace import (
    EQUALS_CANDIDATE,
    EQUALS_ORIGINAL,
    PrivateFileState,
    VerifiedReplaceError,
    apply_verified_replace,
    classify_bytes,
    rollback_verified_replace,
    snapshot_private_file,
)

PRODUCTION_COMPLETE = "PRODUCTION_RETIRE_APPLY_COMPLETE"
APPLY_FAILED_ROLLBACK_COMPLETE = "PRODUCTION_APPLY_FAILED_ROLLBACK_COMPLETE"
PRODUCTION_INCIDENT = "PRODUCTION_INCIDENT_ROLLBACK_FAILED"
BLOCKED = "BLOCKED"


def _validate_apply_result(result: Any, *, ordinal: int) -> bool:
    return (
        getattr(result, "ordinal", None) == ordinal
        and getattr(result, "phase", None) == "apply"
        and getattr(result, "classification", None) == EQUALS_CANDIDATE
        and getattr(result, "parent_fsync_performed", None) is True
    )


def _validate_rollback_result(result: Any, *, ordinal: int) -> bool:
    return (
        getattr(result, "ordinal", None) == ordinal
        and getattr(result, "phase", None) == "rollback"
        and getattr(result, "classification", None) == EQUALS_ORIGINAL
        and getattr(result, "parent_fsync_performed", None) is True
    )


def _public_replace_results(results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": getattr(result, "ordinal", None),
            "phase": getattr(result, "phase", None),
            "classification": getattr(result, "classification", None),
            "parent_fsync_performed": getattr(
                result,
                "parent_fsync_performed",
                False,
            ),
        }
        for result in results
    ]


def _target_exact_original(state: PrivateFileState) -> bool:
    try:
        info = state.target_path.lstat()
        actual = state.target_path.read_bytes()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False
    return (
        classify_bytes(
            actual,
            state.candidate_bytes,
            state.original_bytes,
        )
        == EQUALS_ORIGINAL
        and stat.S_IMODE(info.st_mode) == state.target_mode
        and info.st_uid == state.target_uid
        and info.st_gid == state.target_gid
    )


def rollback_after_failure(
    *,
    states: list[PrivateFileState],
    changed_ordinals: set[int],
    docker: str,
    container: str,
    expected_version: str,
    scheduler_before: Path,
    scheduler_current: Path,
    prewrite_timer_state: str,
    restart_attempted: bool,
) -> dict[str, Any]:
    rollback_results: list[Any] = []

    for ordinal in sorted(changed_ordinals, reverse=True):
        state = states[ordinal - 1]
        result = rollback_verified_replace(state, ordinal=ordinal)
        if not _validate_rollback_result(result, ordinal=ordinal):
            raise common.GateError("ROLLBACK_BOUNDED_VERIFICATION_FAILED")
        rollback_results.append(result)

    if not all(_target_exact_original(state) for state in states):
        raise common.GateError("ROLLBACK_ORIGINALS_NOT_EXACT")
    if not common.check_live_config(docker, container):
        raise common.GateError("ROLLBACK_LIVE_CHECK_CONFIG_FAILED")

    restart_report: dict[str, bool] | None = None
    if restart_attempted:
        before = common.container_started_at(docker, container)
        restart_report = common.restart_and_wait(
            docker=docker,
            container=container,
            expected_version=expected_version,
            before_started_at=before,
        )
        scheduler_report = preflight.stable_scheduler_semantic_invariant(
            scheduler_before,
            scheduler_current,
        )
    else:
        scheduler_report = preflight.strict_scheduler_invariant(
            scheduler_before,
            scheduler_current,
        )

    runtime_report, _timer = common.collect_runtime_safety(
        docker=docker,
        container=container,
        expected_timer_state=prewrite_timer_state,
        require_legacy_count=True,
    )

    return {
        "bounded_results": _public_replace_results(rollback_results),
        "all_bounded_originals_exact": True,
        "live_check_config_passed": True,
        "restart_attempted": restart_attempted,
        "restart": restart_report,
        "scheduler_semantic_passed": True,
        "scheduler_bytes_equal_required": not restart_attempted,
        "scheduler_bytes_equal": scheduler_report.get("scheduler", {}).get(
            "bytes_equal"
        ),
        "runtime_safety": runtime_report,
        "full_state_machine_equivalence_claimed": False,
    }


def _base_report(
    *,
    decision: str,
    reason: str | None,
    authorization_consumed: bool,
    production_change: bool,
    restart_attempted: bool,
    rollback_retained: bool,
) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": decision,
        "reason": reason,
        "gate": common.GATE_ID,
        "authorization_consumed": authorization_consumed,
        "production_change": production_change,
        "rollback_material_retained": rollback_retained,
        "privacy": common.privacy_report(),
        "production_mutation": common.mutation_report(
            config_written=production_change,
            restart_attempted=restart_attempted,
        ),
        "scheduler_storage_written": False,
        "scheduler_service_called": False,
        "full_state_machine_equivalence_claimed": False,
    }


def blocked_report(reason: str) -> dict[str, Any]:
    return _base_report(
        decision=BLOCKED,
        reason=reason,
        authorization_consumed=False,
        production_change=False,
        restart_attempted=False,
        rollback_retained=False,
    )


def execute_gate(args: argparse.Namespace) -> dict[str, Any]:
    authorization_consumed = False
    changed_ordinals: set[int] = set()
    restart_attempted = False
    rollback_dir: Path | None = None
    scheduler_before: Path | None = None
    states: list[PrivateFileState] = []
    prewrite_timer_state: str | None = None

    try:
        common.authorization_precheck(
            marker_base=common.AUTH_MARKER_BASE,
            expected_sha=args.expected_sha,
            authorization=args.authorization,
        )

        source = common.source_gate(
            args.expected_sha,
            args.expected_tree,
            args.expected_parent,
        )
        if not all(source.values()):
            raise common.GateError("EXACT_SOURCE_GATE_FAILED")
        if common.expected_version_from_source() != args.expected_version:
            raise common.GateError("EXPECTED_VERSION_SOURCE_MISMATCH")
        if (
            common.running_version(args.docker, args.container)
            != args.expected_version
        ):
            raise common.GateError("RUNNING_HOME_ASSISTANT_VERSION_MISMATCH")

        preflight.run_fresh_dry_run(
            docker=args.docker,
            container=args.container,
            expected_sha=args.expected_sha,
            expected_tree=args.expected_tree,
            expected_parent=args.expected_parent,
            expected_version=args.expected_version,
        )
        pre_runtime, prewrite_timer_state = common.collect_runtime_safety(
            docker=args.docker,
            container=args.container,
            expected_timer_state=None,
            require_legacy_count=True,
        )

        config_root = common.discover_config_root(args.docker, args.container)
        live_one = config_root / LIVE_ONE_RELATIVE
        live_two = config_root / LIVE_TWO_RELATIVE
        scheduler_current = config_root / SCHEDULER_STORAGE_RELATIVE
        if not common.check_live_config(args.docker, args.container):
            raise common.GateError("PREWRITE_LIVE_CHECK_CONFIG_FAILED")

        with tempfile.TemporaryDirectory(
            prefix="ha-heater-retire-production-"
        ) as tmp:
            private_temp = Path(tmp)
            os.chmod(private_temp, 0o700)
            candidate_one, candidate_two, materialization = (
                preflight.materialize_candidates(
                    docker=args.docker,
                    container=args.container,
                    host_output=private_temp / "materialized",
                )
            )
            states = [
                snapshot_private_file(candidate_one, live_one),
                snapshot_private_file(candidate_two, live_two),
            ]

            second_source = common.source_gate(
                args.expected_sha,
                args.expected_tree,
                args.expected_parent,
            )
            if not all(second_source.values()):
                raise common.GateError("PREWRITE_SOURCE_DRIFTED")
            preflight.run_fresh_dry_run(
                docker=args.docker,
                container=args.container,
                expected_sha=args.expected_sha,
                expected_tree=args.expected_tree,
                expected_parent=args.expected_parent,
                expected_version=args.expected_version,
            )

            # Comparing the same live file validates Scheduler structure and
            # the required empty list before retaining the prewrite snapshot.
            preflight.strict_scheduler_invariant(
                scheduler_current,
                scheduler_current,
            )
            rollback_dir, scheduler_before = preflight.create_rollback_bundle(
                rollback_base=common.ROLLBACK_BASE,
                gate_id=common.GATE_ID,
                expected_sha=args.expected_sha,
                expected_tree=args.expected_tree,
                expected_parent=args.expected_parent,
                expected_version=args.expected_version,
                states=states,
                scheduler_storage=scheduler_current,
            )
            preflight.strict_scheduler_invariant(
                scheduler_before,
                scheduler_current,
            )

            common.authorization_precheck(
                marker_base=common.AUTH_MARKER_BASE,
                expected_sha=args.expected_sha,
                authorization=args.authorization,
            )
            try:
                common.consume_authorization(
                    marker_base=common.AUTH_MARKER_BASE,
                    expected_sha=args.expected_sha,
                    authorization=args.authorization,
                )
            except common.GateError:
                authorization_consumed = (
                    common.authorization_marker_consumed(
                        common.AUTH_MARKER_BASE,
                        args.expected_sha,
                    )
                )
                raise
            authorization_consumed = True

            apply_results: list[Any] = []
            try:
                for ordinal, state in enumerate(states, start=1):
                    try:
                        result = apply_verified_replace(
                            state,
                            ordinal=ordinal,
                        )
                    except VerifiedReplaceError as exc:
                        if exc.target_may_have_changed:
                            changed_ordinals.add(ordinal)
                        raise
                    if not _validate_apply_result(result, ordinal=ordinal):
                        changed_ordinals.add(ordinal)
                        raise common.GateError(
                            "BOUNDED_APPLY_VERIFICATION_FAILED"
                        )
                    changed_ordinals.add(ordinal)
                    apply_results.append(result)

                if not common.check_live_config(args.docker, args.container):
                    raise common.GateError("LIVE_CHECK_CONFIG_FAILED")
                scheduler_pre_restart = (
                    preflight.strict_scheduler_invariant(
                        scheduler_before,
                        scheduler_current,
                    )
                )

                before_started_at = common.container_started_at(
                    args.docker,
                    args.container,
                )
                # Conservatively mark the restart before invoking Docker. Any
                # partial or failed restart must force rollback to restart the
                # restored originals.
                restart_attempted = True
                restart_report = common.restart_and_wait(
                    docker=args.docker,
                    container=args.container,
                    expected_version=args.expected_version,
                    before_started_at=before_started_at,
                )
                scheduler_post_restart = (
                    preflight.stable_scheduler_semantic_invariant(
                        scheduler_before,
                        scheduler_current,
                    )
                )
                post_runtime, _timer = common.collect_runtime_safety(
                    docker=args.docker,
                    container=args.container,
                    expected_timer_state=prewrite_timer_state,
                    require_legacy_count=False,
                )

                report = _base_report(
                    decision=PRODUCTION_COMPLETE,
                    reason=None,
                    authorization_consumed=True,
                    production_change=True,
                    restart_attempted=True,
                    rollback_retained=True,
                )
                report.update(
                    {
                        "source_gate": source,
                        "fresh_hardened_dry_run_passed": True,
                        "prewrite_runtime_safety": pre_runtime,
                        "private_materialization_passed": (
                            materialization.get("decision")
                            == "PRIVATE_RETIRE_CANDIDATE_MATERIALIZED"
                        ),
                        "prewrite_scheduler_empty": True,
                        "bounded_apply": {
                            "verified_count": len(apply_results),
                            "results": _public_replace_results(apply_results),
                        },
                        "live_check_config_passed": True,
                        "scheduler_before_restart": {
                            "semantic_passed": True,
                            "bytes_equal": scheduler_pre_restart.get(
                                "scheduler", {}
                            ).get("bytes_equal"),
                        },
                        "restart": restart_report,
                        "scheduler_after_restart": {
                            "semantic_passed": True,
                            "bytes_equal_evidence": scheduler_post_restart.get(
                                "scheduler", {}
                            ).get("bytes_equal"),
                            "raw_byte_identity_required_for_pass": False,
                        },
                        "post_restart_runtime_safety": post_runtime,
                        "current_candidate_active": True,
                    }
                )
                return report

            except Exception as exc:
                reason = (
                    exc.reason
                    if isinstance(
                        exc,
                        (common.GateError, VerifiedReplaceError),
                    )
                    else "PRODUCTION_GATE_UNEXPECTED_ERROR"
                )

                if changed_ordinals:
                    try:
                        assert scheduler_before is not None
                        assert prewrite_timer_state is not None
                        rollback = rollback_after_failure(
                            states=states,
                            changed_ordinals=changed_ordinals,
                            docker=args.docker,
                            container=args.container,
                            expected_version=args.expected_version,
                            scheduler_before=scheduler_before,
                            scheduler_current=scheduler_current,
                            prewrite_timer_state=prewrite_timer_state,
                            restart_attempted=restart_attempted,
                        )
                    except Exception as rollback_exc:
                        rollback_reason = (
                            rollback_exc.reason
                            if isinstance(
                                rollback_exc,
                                (
                                    common.GateError,
                                    VerifiedReplaceError,
                                ),
                            )
                            else "ROLLBACK_UNEXPECTED_ERROR"
                        )
                        report = _base_report(
                            decision=PRODUCTION_INCIDENT,
                            reason=rollback_reason,
                            authorization_consumed=authorization_consumed,
                            production_change=True,
                            restart_attempted=restart_attempted,
                            rollback_retained=rollback_dir is not None,
                        )
                        report["apply_failure_reason"] = reason
                        report["current_candidate_active"] = None
                        return report

                    report = _base_report(
                        decision=APPLY_FAILED_ROLLBACK_COMPLETE,
                        reason=reason,
                        authorization_consumed=authorization_consumed,
                        production_change=True,
                        restart_attempted=(
                            restart_attempted
                            or rollback.get("restart_attempted") is True
                        ),
                        rollback_retained=rollback_dir is not None,
                    )
                    report["rollback"] = rollback
                    report["current_candidate_active"] = False
                    return report

                authorization_consumed = (
                    authorization_consumed
                    or common.authorization_marker_consumed(
                        common.AUTH_MARKER_BASE,
                        args.expected_sha,
                    )
                )
                report = _base_report(
                    decision=BLOCKED,
                    reason=reason,
                    authorization_consumed=authorization_consumed,
                    production_change=False,
                    restart_attempted=restart_attempted,
                    rollback_retained=rollback_dir is not None,
                )
                report["current_candidate_active"] = False
                return report

    except common.GateError as exc:
        authorization_consumed = (
            authorization_consumed
            or common.authorization_marker_consumed(
                common.AUTH_MARKER_BASE,
                args.expected_sha,
            )
        )
        report = _base_report(
            decision=BLOCKED,
            reason=exc.reason,
            authorization_consumed=authorization_consumed,
            production_change=False,
            restart_attempted=restart_attempted,
            rollback_retained=rollback_dir is not None,
        )
        report["current_candidate_active"] = False
        return report
    except Exception as exc:
        _ = exc
        authorization_consumed = (
            authorization_consumed
            or common.authorization_marker_consumed(
                common.AUTH_MARKER_BASE,
                args.expected_sha,
            )
        )
        report = _base_report(
            decision=BLOCKED,
            reason="PRODUCTION_GATE_UNEXPECTED_ERROR",
            authorization_consumed=authorization_consumed,
            production_change=False,
            restart_attempted=restart_attempted,
            rollback_retained=rollback_dir is not None,
        )
        report["current_candidate_active"] = False
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container")
    parser.add_argument("--expected-sha")
    parser.add_argument("--expected-tree")
    parser.add_argument("--expected-parent")
    parser.add_argument("--expected-version")
    parser.add_argument("--authorization", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--retire", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if not args.execute or not args.retire:
            report = blocked_report("EXPLICIT_PRODUCTION_EXECUTION_GATE_REQUIRED")
        elif any(
            not isinstance(value, str) or not value
            for value in (
                args.container,
                args.expected_sha,
                args.expected_tree,
                args.expected_parent,
                args.expected_version,
                args.authorization,
            )
        ):
            report = blocked_report("PRODUCTION_GATE_ARGUMENTS_REQUIRED")
        else:
            report = execute_gate(args)
    except common.GateError as exc:
        report = blocked_report(exc.reason)
    except Exception as exc:
        _ = exc
        report = blocked_report("PRODUCTION_GATE_UNEXPECTED_ERROR")

    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report.get("decision") == PRODUCTION_COMPLETE else 20


if __name__ == "__main__":
    raise SystemExit(main())
