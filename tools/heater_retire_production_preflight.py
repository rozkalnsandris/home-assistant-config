#!/usr/bin/env python3
"""Prewrite, rollback-retention and Scheduler helpers for heater RETIRE."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.heater_retire_production_common import (
    GateError,
    all_false,
    ensure_private_directory,
    fsync_directory,
    run,
)
from tools.run_heater_retire_hardened_dry_run_impl import (
    LIVE_ONE_RELATIVE,
    LIVE_TWO_RELATIVE,
)
from tools.verify_bounded_private_file_replace import PrivateFileState
from tools.verify_scheduler_storage_semantics import (
    PASS as SCHEDULER_PASS,
    compare_scheduler_storage,
)

DRY_RUN_READY = "READY_FOR_NEW_EXPLICIT_RETIRE_PATH_PRODUCTION_APPLY_GATE"
DRY_RUN_WORKER_READY = "HARDENED_RETIRE_DRY_RUN_COMPLETE"
SEMANTIC_ATTEMPTS = 16
SEMANTIC_STABLE_PASSES = 2
POLL_SECONDS = 1.0


def validate_dry_run_report(report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    if report.get("decision") != DRY_RUN_READY:
        return False
    if report.get("live_preconditions_ready") is not True:
        return False
    if report.get("owner_semantic_choice") != "RETIRE":
        return False
    if report.get("private_temp_cleanup") is not True:
        return False
    if report.get("production_apply_authorized") is not False:
        return False
    if report.get("scheduler_bootstrap_authorized") is not False:
        return False
    if not all_false(report.get("privacy")):
        return False
    if not all_false(report.get("production_mutation")):
        return False

    source = report.get("source_gate")
    ha = report.get("home_assistant")
    worker = report.get("worker")
    if not isinstance(source, dict) or not all(
        source.get(key) is True
        for key in ("sha_match", "tree_match", "parent_match")
    ):
        return False
    if not isinstance(ha, dict) or not all(
        ha.get(key) is True
        for key in (
            "expected_version_match",
            "running_version_match",
            "runtime_unchanged",
        )
    ):
        return False
    if not isinstance(worker, dict):
        return False
    if worker.get("decision") != DRY_RUN_WORKER_READY:
        return False
    if worker.get("full_temp_check_config_passed") is not True:
        return False
    if not all_false(worker.get("privacy")):
        return False
    if not all_false(worker.get("production_mutation")):
        return False

    live = worker.get("live_invariants")
    if not isinstance(live, dict) or not all(
        live.get(key) is True
        for key in (
            "heater_files_unchanged",
            "scheduler_storage_empty_after",
            "scheduler_storage_unchanged",
        )
    ):
        return False

    apply = worker.get("hardened_apply")
    rollback = worker.get("hardened_rollback")
    if not isinstance(apply, dict) or apply.get("verified_count") != 2:
        return False
    if not isinstance(rollback, dict) or rollback.get("verified_count") != 2:
        return False
    if rollback.get("temp_originals_restored") is not True:
        return False

    apply_results = apply.get("results")
    rollback_results = rollback.get("results")
    if not isinstance(apply_results, list) or len(apply_results) != 2:
        return False
    if not isinstance(rollback_results, list) or len(rollback_results) != 2:
        return False

    for ordinal, item in enumerate(apply_results, start=1):
        if not isinstance(item, dict):
            return False
        if (
            item.get("ordinal") != ordinal
            or item.get("phase") != "apply"
            or item.get("classification") != "equals_candidate"
            or item.get("parent_fsync_performed") is not True
        ):
            return False

    for ordinal, item in enumerate(rollback_results, start=1):
        if not isinstance(item, dict):
            return False
        if (
            item.get("ordinal") != ordinal
            or item.get("phase") != "rollback"
            or item.get("classification") != "equals_original"
            or item.get("parent_fsync_performed") is not True
        ):
            return False
    return True


def run_fresh_dry_run(
    *,
    docker: str,
    container: str,
    expected_sha: str,
    expected_tree: str,
    expected_parent: str,
    expected_version: str,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-B",
        str(ROOT / "tools" / "run_heater_retire_hardened_dry_run.py"),
        "--docker",
        docker,
        "--container",
        container,
        "--expected-sha",
        expected_sha,
        "--expected-tree",
        expected_tree,
        "--expected-parent",
        expected_parent,
        "--expected-version",
        expected_version,
        "--dry-run",
        "--retire",
    ]
    result = run(command, cwd=ROOT)
    if result.returncode != 0:
        raise GateError("FRESH_HARDENED_DRY_RUN_BLOCKED")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateError("FRESH_HARDENED_DRY_RUN_INVALID_JSON") from exc
    if not validate_dry_run_report(report):
        raise GateError("FRESH_HARDENED_DRY_RUN_NOT_EXACT_PASS")
    return report


def _docker_cp(
    docker: str,
    source: str,
    destination: str,
    *,
    reason: str,
) -> None:
    result = run([docker, "cp", source, destination])
    if result.returncode != 0:
        raise GateError(reason)


def materialize_candidates(
    *,
    docker: str,
    container: str,
    host_output: Path,
) -> tuple[Path, Path, dict[str, Any]]:
    mktemp = run(
        [
            docker,
            "exec",
            container,
            "mktemp",
            "-d",
            "/tmp/ha-heater-retire-prod-XXXXXXXX",
        ]
    )
    if mktemp.returncode != 0:
        raise GateError("PRIVATE_CONTAINER_TEMP_CREATE_FAILED")
    container_root = mktemp.stdout.strip()
    if not container_root.startswith("/tmp/"):
        raise GateError("PRIVATE_CONTAINER_TEMP_INVALID")

    try:
        tool = ROOT / "tools" / "materialize_heater_retire_candidate_privately.py"
        candidate_one = ROOT / LIVE_ONE_RELATIVE
        candidate_two = ROOT / LIVE_TWO_RELATIVE

        _docker_cp(
            docker,
            str(tool),
            f"{container}:{container_root}/materialize.py",
            reason="PRIVATE_MATERIALIZER_STAGE_FAILED",
        )
        _docker_cp(
            docker,
            str(candidate_one),
            f"{container}:{container_root}/candidate-one.yaml",
            reason="FIRST_CANDIDATE_STAGE_FAILED",
        )
        _docker_cp(
            docker,
            str(candidate_two),
            f"{container}:{container_root}/candidate-two.yaml",
            reason="SECOND_CANDIDATE_STAGE_FAILED",
        )
        mkdir = run(
            [
                docker,
                "exec",
                container,
                "mkdir",
                "-m",
                "700",
                f"{container_root}/out",
            ]
        )
        if mkdir.returncode != 0:
            raise GateError("PRIVATE_CONTAINER_OUTPUT_CREATE_FAILED")

        materialize = run(
            [
                docker,
                "exec",
                container,
                "python",
                f"{container_root}/materialize.py",
                "--config-root",
                "/config",
                "--live-legacy",
                "/config/packages/silditajs.yaml",
                "--candidate-one",
                f"{container_root}/candidate-one.yaml",
                "--candidate-two",
                f"{container_root}/candidate-two.yaml",
                "--output-dir",
                f"{container_root}/out",
                "--materialize",
                "--retire",
            ]
        )
        if materialize.returncode != 0:
            raise GateError("PRIVATE_RETIRE_MATERIALIZATION_BLOCKED")
        try:
            report = json.loads(materialize.stdout)
        except json.JSONDecodeError as exc:
            raise GateError("PRIVATE_RETIRE_MATERIALIZATION_INVALID_JSON") from exc
        if report.get("decision") != "PRIVATE_RETIRE_CANDIDATE_MATERIALIZED":
            raise GateError("PRIVATE_RETIRE_MATERIALIZATION_NOT_READY")
        if not all_false(report.get("privacy")):
            raise GateError("PRIVATE_RETIRE_MATERIALIZATION_PRIVACY_FAILED")
        if not all_false(report.get("production_mutation")):
            raise GateError("PRIVATE_RETIRE_MATERIALIZATION_MUTATION_FAILED")

        host_output.mkdir(mode=0o700, parents=True, exist_ok=False)
        first = host_output / "candidate-1.yaml"
        second = host_output / "candidate-2.yaml"
        _docker_cp(
            docker,
            f"{container}:{container_root}/out/candidate-1.yaml",
            str(first),
            reason="FIRST_MATERIALIZED_CANDIDATE_COPY_FAILED",
        )
        _docker_cp(
            docker,
            f"{container}:{container_root}/out/candidate-2.yaml",
            str(second),
            reason="SECOND_MATERIALIZED_CANDIDATE_COPY_FAILED",
        )
        for path in (first, second):
            try:
                info = path.lstat()
            except OSError as exc:
                raise GateError("MATERIALIZED_CANDIDATE_INVALID") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise GateError("MATERIALIZED_CANDIDATE_INVALID")
            os.chmod(path, 0o600)
        return first, second, report
    finally:
        run([docker, "exec", container, "rm", "-rf", "--", container_root])


def _write_private_bytes(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    content = (
        json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    _write_private_bytes(path, content)


def create_rollback_bundle(
    *,
    rollback_base: Path,
    gate_id: str,
    expected_sha: str,
    expected_tree: str,
    expected_parent: str,
    expected_version: str,
    states: list[PrivateFileState],
    scheduler_storage: Path,
) -> tuple[Path, Path]:
    ensure_private_directory(rollback_base)
    rollback_dir = Path(
        tempfile.mkdtemp(prefix="apply-", dir=str(rollback_base))
    )
    os.chmod(rollback_dir, 0o700)

    if len(states) != 2:
        raise GateError("BOUNDED_STATE_COUNT_INVALID")

    original_one = rollback_dir / "ordinal-1.original"
    original_two = rollback_dir / "ordinal-2.original"
    scheduler_before = rollback_dir / "scheduler.before"

    _write_private_bytes(original_one, states[0].original_bytes)
    _write_private_bytes(original_two, states[1].original_bytes)
    try:
        scheduler_bytes = scheduler_storage.read_bytes()
    except OSError as exc:
        raise GateError("SCHEDULER_STORAGE_SNAPSHOT_FAILED") from exc
    _write_private_bytes(scheduler_before, scheduler_bytes)

    metadata = {
        "schema": 1,
        "gate": gate_id,
        "source": {
            "sha": expected_sha,
            "tree": expected_tree,
            "parent": expected_parent,
            "home_assistant_version": expected_version,
        },
        "bounded": [
            {
                "ordinal": index,
                "mode": state.target_mode,
                "uid": state.target_uid,
                "gid": state.target_gid,
            }
            for index, state in enumerate(states, start=1)
        ],
        "scheduler_write_authorized": False,
        "retained_cleanup_authorized": False,
    }
    _write_private_json(rollback_dir / "metadata.json", metadata)

    if not hmac.compare_digest(original_one.read_bytes(), states[0].original_bytes):
        raise GateError("ROLLBACK_ORDINAL_1_PERSISTENCE_MISMATCH")
    if not hmac.compare_digest(original_two.read_bytes(), states[1].original_bytes):
        raise GateError("ROLLBACK_ORDINAL_2_PERSISTENCE_MISMATCH")
    if not hmac.compare_digest(scheduler_before.read_bytes(), scheduler_bytes):
        raise GateError("ROLLBACK_SCHEDULER_PERSISTENCE_MISMATCH")

    fsync_directory(rollback_dir)
    fsync_directory(rollback_base)
    return rollback_dir, scheduler_before


def strict_scheduler_invariant(
    before: Path,
    current: Path,
) -> dict[str, Any]:
    report = compare_scheduler_storage(before, current)
    scheduler = report.get("scheduler") if isinstance(report, dict) else None
    if (
        report.get("decision") != SCHEDULER_PASS
        or not isinstance(scheduler, dict)
        or scheduler.get("before_schedule_count") != 0
        or scheduler.get("current_schedule_count") != 0
        or scheduler.get("bytes_equal") is not True
    ):
        raise GateError("SCHEDULER_STRICT_PRE_RESTART_INVARIANT_FAILED")
    return report


def stable_scheduler_semantic_invariant(
    before: Path,
    current: Path,
) -> dict[str, Any]:
    consecutive = 0
    last: dict[str, Any] | None = None
    for _attempt in range(SEMANTIC_ATTEMPTS):
        report = compare_scheduler_storage(before, current)
        last = report
        scheduler = report.get("scheduler") if isinstance(report, dict) else None
        passed = (
            report.get("decision") == SCHEDULER_PASS
            and isinstance(scheduler, dict)
            and scheduler.get("before_schedule_count") == 0
            and scheduler.get("current_schedule_count") == 0
            and scheduler.get("schedules_equal") is True
        )
        if passed:
            consecutive += 1
            if consecutive >= SEMANTIC_STABLE_PASSES:
                return report
        else:
            consecutive = 0
        time.sleep(POLL_SECONDS)

    reason = last.get("reason") if isinstance(last, dict) else None
    if reason == "NONEMPTY_PREWRITE_REQUIRES_RESTART_AWARE_VERIFICATION":
        raise GateError("NONEMPTY_PREWRITE_REQUIRES_RESTART_AWARE_VERIFICATION")
    raise GateError("POST_RESTART_SCHEDULER_SEMANTIC_INVARIANT_FAILED")
