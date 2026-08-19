#!/usr/bin/env python3
"""Fresh privacy-safe hardened RETIRE dry run for the heater transition."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any

from tools.materialize_heater_retire_candidate_privately import (
    MaterializationError,
    extract_live_target,
    materialize_candidate_texts,
)
from tools.verify_bounded_private_file_replace import (
    EQUALS_CANDIDATE,
    EQUALS_ORIGINAL,
    VerifiedReplaceError,
    apply_verified_sequence,
    rollback_verified_replace,
    snapshot_private_file,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"

READY = "READY_FOR_NEW_EXPLICIT_RETIRE_PATH_PRODUCTION_APPLY_GATE"
WORKER_READY = "HARDENED_RETIRE_DRY_RUN_COMPLETE"
BLOCKED = "BLOCKED"

LIVE_ONE_RELATIVE = Path("packages") / "silditajs.yaml"
LIVE_TWO_RELATIVE = Path("packages") / "heater_scheduler.yaml"
SCHEDULER_STORAGE_RELATIVE = Path(".storage") / "scheduler.storage"

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024

EXCLUDED_TOP_DIRS = {
    "backup",
    "backups",
    "deps",
    "media",
    "share",
    "tts",
    "www",
}
EXCLUDED_ANY_DIRS = {
    ".cache",
    ".git",
    "__pycache__",
}


def privacy_report() -> dict[str, bool]:
    return {
        "entity_ids_or_targets_emitted": False,
        "secret_aliases_emitted": False,
        "secret_values_emitted": False,
        "hashes_emitted": False,
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "latent_schedule_values_emitted": False,
        "schedule_days_or_times_emitted": False,
    }


def production_mutation_report() -> dict[str, bool]:
    return {
        "home_assistant_config_written": False,
        "scheduler_service_called": False,
        "scheduler_storage_written": False,
        "helper_state_changed": False,
        "heater_actuated": False,
        "reload_or_restart": False,
    }


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": BLOCKED,
        "reason": reason,
        "privacy": privacy_report(),
        "production_mutation": production_mutation_report(),
    }


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        check=False,
    )


def _git_value(args: list[str]) -> str:
    result = _run(["git", *args], cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError("SOURCE_GIT_PROBE_FAILED")
    return result.stdout.strip()


def source_gate(
    expected_sha: str,
    expected_tree: str,
    expected_parent: str,
) -> dict[str, Any]:
    actual_sha = _git_value(["rev-parse", "HEAD"])
    actual_tree = _git_value(["rev-parse", "HEAD^{tree}"])
    actual_parent = _git_value(["rev-parse", "HEAD^"])
    return {
        "sha_match": hmac.compare_digest(actual_sha, expected_sha),
        "tree_match": hmac.compare_digest(actual_tree, expected_tree),
        "parent_match": hmac.compare_digest(actual_parent, expected_parent),
    }


def _expected_version(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("EXPECTED_VERSION_INVALID")
    return value


def _running_version(docker: str, container: str) -> str:
    result = _run(
        [
            docker,
            "exec",
            container,
            "python",
            "-c",
            "import homeassistant; print(homeassistant.__version__)",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError("HA_VERSION_PROBE_FAILED")
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("HA_VERSION_PROBE_FAILED")
    return value


def _inspect_value(docker: str, container: str, template: str) -> str:
    result = _run([docker, "inspect", "-f", template, container])
    if result.returncode != 0:
        raise RuntimeError("HA_CONTAINER_INSPECT_FAILED")
    return result.stdout.strip()


def validate_reconciliation_report(data: Any) -> bool:
    if not isinstance(data, dict):
        return False

    ready = (
        "READY_FOR_OWNER_CHOICE_"
        "PRESERVE_OR_RETIRE_LATENT_LEGACY_SCHEDULE"
    )
    if data.get("decision") != ready or data.get("reasons") != []:
        return False

    current = data.get("current_behavior")
    binding = data.get("binding_reconciliation")
    source = data.get("source_reconciliation")
    transition = data.get("transition")
    private = data.get("private_resolution")
    ha = data.get("home_assistant")

    if not all(
        isinstance(section, dict)
        for section in (current, binding, source, transition, private, ha)
    ):
        return False

    required_true = (
        ha.get("version_match"),
        current.get("legacy_schedule_helper_off"),
        current.get("scheduler_storage_empty"),
        current.get("latent_legacy_on_time_valid"),
        current.get("latent_legacy_off_time_valid"),
        binding.get("resolved_value_equality_proof_succeeded"),
        binding.get("all_four_targets_equal"),
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
    if current.get("legacy_automation_enabled_count") != 2:
        return False

    for section_name in ("privacy", "mutation", "claims"):
        section = data.get(section_name)
        if not isinstance(section, dict):
            return False
        if any(value is not False for value in section.values()):
            return False
    return True


def collect_reconciliation(docker: str, container: str) -> dict[str, Any]:
    result = _run(
        [
            sys.executable,
            "-m",
            "tools.reconcile_heater_binding_template_privately",
            "--docker",
            docker,
            "--container",
            container,
            "--reconcile",
            "--stdout",
        ],
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError("LIVE_RECONCILIATION_BLOCKED")
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LIVE_RECONCILIATION_INVALID_JSON") from exc
    if not validate_reconciliation_report(decoded):
        raise RuntimeError("LIVE_RECONCILIATION_NOT_READY")
    return decoded


def _require_regular_non_symlink(path: Path, reason: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError(reason) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(reason)
    return info


def _require_directory_non_symlink(path: Path, reason: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError(reason) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError(reason)
    return info


def _read_regular_bytes(path: Path, reason: str) -> bytes:
    _require_regular_non_symlink(path, reason)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeError(reason) from exc


def _write_private_new(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def scheduler_storage_empty(path: Path) -> bool:
    try:
        raw = _read_regular_bytes(path, "SCHEDULER_STORAGE_INVALID")
        payload = json.loads(raw.decode("utf-8"))
    except (RuntimeError, UnicodeError, json.JSONDecodeError):
        return False
    data = payload.get("data") if isinstance(payload, dict) else None
    schedules = data.get("schedules") if isinstance(data, dict) else None
    return isinstance(schedules, list) and len(schedules) == 0


def _excluded_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("home-assistant_v2.db")
        or name.startswith("home-assistant.log")
        or name.endswith(".journal")
        or name.endswith(".tmp")
    )


def copy_private_config_tree(source: Path, destination: Path) -> None:
    _require_directory_non_symlink(source, "CONFIG_ROOT_INVALID")
    if destination.exists():
        raise RuntimeError("TEMP_CONFIG_DESTINATION_EXISTS")
    destination.mkdir(mode=0o700, parents=True)
    total = 0

    for root_raw, dirs, files in os.walk(source, topdown=True, followlinks=False):
        root = Path(root_raw)
        kept_dirs: list[str] = []
        for name in dirs:
            child = root / name
            rel = child.relative_to(source)
            if rel.parts and rel.parts[0] in EXCLUDED_TOP_DIRS:
                continue
            if name in EXCLUDED_ANY_DIRS:
                continue
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RuntimeError("CONFIG_TREE_DIRECTORY_INVALID")
            kept_dirs.append(name)
            (destination / rel).mkdir(mode=0o700, parents=True, exist_ok=True)
        dirs[:] = kept_dirs

        for name in files:
            source_file = root / name
            rel = source_file.relative_to(source)
            if rel.parts and rel.parts[0] in EXCLUDED_TOP_DIRS:
                continue
            if _excluded_file(source_file):
                continue
            info = source_file.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise RuntimeError("CONFIG_TREE_FILE_INVALID")
            if info.st_size > MAX_FILE_BYTES:
                raise RuntimeError("CONFIG_TREE_FILE_TOO_LARGE")
            total += info.st_size
            if total > MAX_TOTAL_BYTES:
                raise RuntimeError("CONFIG_TREE_TOTAL_TOO_LARGE")
            target = destination / rel
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with source_file.open("rb") as source_handle:
                with target.open("xb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
            target.chmod(0o600)


def _load_yaml_dict(path: Path, secrets_root: Path) -> dict[str, Any]:
    try:
        from homeassistant.util.yaml import Secrets, load_yaml_dict
    except ImportError as exc:
        raise RuntimeError("HOME_ASSISTANT_YAML_LOADER_UNAVAILABLE") from exc
    try:
        loaded = load_yaml_dict(str(path), Secrets(secrets_root))
    except Exception as exc:
        raise RuntimeError("HOME_ASSISTANT_YAML_LOAD_FAILED") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError("HOME_ASSISTANT_YAML_TOP_LEVEL_INVALID")
    return loaded


def _check_config(config_root: Path) -> bool:
    result = _run(
        [
            sys.executable,
            "-m",
            "homeassistant",
            "--script",
            "check_config",
            "--config",
            str(config_root),
            "--fail-on-warnings",
        ]
    )
    return result.returncode == 0


def _classification_reports(results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": result.ordinal,
            "phase": result.phase,
            "classification": result.classification,
            "parent_fsync_performed": result.parent_fsync_performed,
        }
        for result in results
    ]


def worker_dry_run(
    config_root: Path,
    staged_root: Path,
    workspace: Path,
) -> dict[str, Any]:
    live_one = config_root / LIVE_ONE_RELATIVE
    live_two = config_root / LIVE_TWO_RELATIVE
    scheduler_storage = config_root / SCHEDULER_STORAGE_RELATIVE
    candidate_one = staged_root / LIVE_ONE_RELATIVE
    candidate_two = staged_root / LIVE_TWO_RELATIVE

    live_one_before = _read_regular_bytes(live_one, "LIVE_ORDINAL_1_INVALID")
    live_two_before = _read_regular_bytes(live_two, "LIVE_ORDINAL_2_INVALID")
    scheduler_before = _read_regular_bytes(
        scheduler_storage, "SCHEDULER_STORAGE_INVALID"
    )
    if not scheduler_storage_empty(scheduler_storage):
        raise RuntimeError("SCHEDULER_STORAGE_NOT_EMPTY")
    _require_directory_non_symlink(workspace, "WORKSPACE_INVALID")

    materialized_dir = workspace / "materialized"
    materialized_dir.mkdir(mode=0o700)
    live_loaded = _load_yaml_dict(live_one, config_root)
    private_target = extract_live_target(live_loaded)
    if private_target is None:
        raise RuntimeError("PRIVATE_LIVE_TARGETS_NOT_EQUAL_OR_UNRESOLVED")

    try:
        first_text = candidate_one.read_text(encoding="utf-8")
        second_text = candidate_two.read_text(encoding="utf-8")
        first_out, second_out, counts = materialize_candidate_texts(
            first_text, second_text, private_target
        )
    except (OSError, UnicodeError, MaterializationError) as exc:
        if isinstance(exc, MaterializationError):
            raise RuntimeError(exc.reason) from exc
        raise RuntimeError("PRIVATE_MATERIALIZATION_RUNTIME_ERROR") from exc

    materialized_one = materialized_dir / "candidate-1.yaml"
    materialized_two = materialized_dir / "candidate-2.yaml"
    _write_private_new(materialized_one, first_out)
    _write_private_new(materialized_two, second_out)
    _load_yaml_dict(materialized_one, config_root)
    _load_yaml_dict(materialized_two, config_root)

    temp_config = workspace / "config-copy"
    copy_private_config_tree(config_root, temp_config)
    temp_one = temp_config / LIVE_ONE_RELATIVE
    temp_two = temp_config / LIVE_TWO_RELATIVE

    if not hmac.compare_digest(
        _read_regular_bytes(temp_one, "TEMP_ORDINAL_1_INVALID"),
        live_one_before,
    ):
        raise RuntimeError("TEMP_ORDINAL_1_ORIGINAL_COPY_MISMATCH")
    if not hmac.compare_digest(
        _read_regular_bytes(temp_two, "TEMP_ORDINAL_2_INVALID"),
        live_two_before,
    ):
        raise RuntimeError("TEMP_ORDINAL_2_ORIGINAL_COPY_MISMATCH")

    states = [
        snapshot_private_file(materialized_one, temp_one),
        snapshot_private_file(materialized_two, temp_two),
    ]
    apply_results: list[Any] = []
    rollback_results: list[Any] = []
    check_config_passed = False
    apply_error: VerifiedReplaceError | None = None
    check_error = False
    rollback_error: VerifiedReplaceError | None = None

    try:
        apply_results = apply_verified_sequence(states)
        if any(
            result.classification != EQUALS_CANDIDATE
            or result.parent_fsync_performed is not True
            for result in apply_results
        ):
            raise RuntimeError("HARDENED_APPLY_RESULT_INVALID")
        check_config_passed = _check_config(temp_config)
        if not check_config_passed:
            check_error = True
    except VerifiedReplaceError as exc:
        apply_error = exc
    finally:
        for ordinal, state in reversed(list(enumerate(states, start=1))):
            try:
                result = rollback_verified_replace(state, ordinal=ordinal)
                rollback_results.append(result)
            except VerifiedReplaceError as exc:
                rollback_error = exc
                break

    rollback_results.sort(key=lambda item: item.ordinal)
    live_unchanged = (
        hmac.compare_digest(
            _read_regular_bytes(live_one, "LIVE_ORDINAL_1_POST_INVALID"),
            live_one_before,
        )
        and hmac.compare_digest(
            _read_regular_bytes(live_two, "LIVE_ORDINAL_2_POST_INVALID"),
            live_two_before,
        )
    )
    scheduler_unchanged = hmac.compare_digest(
        _read_regular_bytes(scheduler_storage, "SCHEDULER_STORAGE_POST_INVALID"),
        scheduler_before,
    )
    scheduler_empty_after = scheduler_storage_empty(scheduler_storage)
    temp_rollback_restored = (
        hmac.compare_digest(
            _read_regular_bytes(temp_one, "TEMP_ORDINAL_1_POST_INVALID"),
            live_one_before,
        )
        and hmac.compare_digest(
            _read_regular_bytes(temp_two, "TEMP_ORDINAL_2_POST_INVALID"),
            live_two_before,
        )
    )

    if apply_error is not None:
        raise RuntimeError(
            "HARDENED_APPLY_BLOCKED_"
            + apply_error.reason
            + "_ORDINAL_"
            + str(apply_error.ordinal)
            + "_"
            + str(apply_error.classification or "unclassified").upper()
        )
    if rollback_error is not None:
        raise RuntimeError(
            "HARDENED_ROLLBACK_BLOCKED_"
            + rollback_error.reason
            + "_ORDINAL_"
            + str(rollback_error.ordinal)
            + "_"
            + str(rollback_error.classification or "unclassified").upper()
        )
    if check_error:
        raise RuntimeError("FULL_TEMP_CHECK_CONFIG_FAILED")
    if len(apply_results) != 2:
        raise RuntimeError("HARDENED_APPLY_COUNT_INVALID")
    if len(rollback_results) != 2:
        raise RuntimeError("HARDENED_ROLLBACK_COUNT_INVALID")
    if any(
        result.classification != EQUALS_ORIGINAL
        or result.parent_fsync_performed is not True
        for result in rollback_results
    ):
        raise RuntimeError("HARDENED_ROLLBACK_RESULT_INVALID")
    if not temp_rollback_restored:
        raise RuntimeError("TEMP_ROLLBACK_CONTENT_NOT_RESTORED")
    if not live_unchanged:
        raise RuntimeError("LIVE_HEATER_FILES_CHANGED")
    if not scheduler_unchanged:
        raise RuntimeError("SCHEDULER_STORAGE_CHANGED")
    if not scheduler_empty_after:
        raise RuntimeError("SCHEDULER_STORAGE_NOT_EMPTY_AFTER")

    return {
        "schema": 1,
        "decision": WORKER_READY,
        "materialization": {
            "retire_mode": True,
            "neutral_initial_used": True,
            "output_files_written_count": 2,
            "materialized_yaml_reload_passed": True,
            **counts,
        },
        "hardened_apply": {
            "verified_count": len(apply_results),
            "results": _classification_reports(apply_results),
        },
        "full_temp_check_config_passed": check_config_passed,
        "hardened_rollback": {
            "verified_count": len(rollback_results),
            "results": _classification_reports(rollback_results),
            "temp_originals_restored": temp_rollback_restored,
        },
        "live_invariants": {
            "heater_files_unchanged": live_unchanged,
            "scheduler_storage_unchanged": scheduler_unchanged,
            "scheduler_storage_empty_after": scheduler_empty_after,
        },
        "privacy": privacy_report(),
        "production_mutation": production_mutation_report(),
    }


def _copy_into_container(
    docker: str,
    container: str,
    source: Path,
    destination: str,
) -> None:
    result = _run([docker, "cp", str(source), f"{container}:{destination}"])
    if result.returncode != 0:
        raise RuntimeError("CONTAINER_STAGE_COPY_FAILED")


def host_dry_run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.dry_run or not args.retire:
        return blocked_report("HARDENED_RETIRE_DRY_RUN_GATE_REQUIRED")

    gate = source_gate(args.expected_sha, args.expected_tree, args.expected_parent)
    if not all(gate.values()):
        return blocked_report("SOURCE_GATE_MISMATCH")
    expected_version = _expected_version(args.expected_version_file)
    if not hmac.compare_digest(expected_version, args.expected_version):
        return blocked_report("EXPECTED_VERSION_FILE_MISMATCH")
    running_version = _running_version(args.docker, args.container)
    if not hmac.compare_digest(running_version, args.expected_version):
        return blocked_report("HA_VERSION_MISMATCH")

    running_before = _inspect_value(args.docker, args.container, "{{.State.Running}}")
    started_before = _inspect_value(args.docker, args.container, "{{.State.StartedAt}}")
    restart_before = _inspect_value(args.docker, args.container, "{{.RestartCount}}")
    if running_before != "true":
        return blocked_report("HA_CONTAINER_NOT_RUNNING")
    collect_reconciliation(args.docker, args.container)

    mktemp = _run(
        [
            args.docker,
            "exec",
            args.container,
            "mktemp",
            "-d",
            "/tmp/ha-heater103-XXXXXXXX",
        ]
    )
    if mktemp.returncode != 0:
        return blocked_report("PRIVATE_TEMP_CREATE_FAILED")
    container_tmp = mktemp.stdout.strip()
    if not container_tmp:
        return blocked_report("PRIVATE_TEMP_CREATE_FAILED")

    worker_report: dict[str, Any] | None = None
    cleanup_ok = False
    try:
        layout = _run(
            [
                args.docker,
                "exec",
                args.container,
                "mkdir",
                "-m",
                "700",
                "-p",
                f"{container_tmp}/repo/tools",
                f"{container_tmp}/repo/packages",
                f"{container_tmp}/workspace",
            ]
        )
        if layout.returncode != 0:
            raise RuntimeError("PRIVATE_TEMP_LAYOUT_FAILED")

        stage_files = (
            (
                ROOT / "tools" / "materialize_heater_retire_candidate_privately.py",
                f"{container_tmp}/repo/tools/materialize_heater_retire_candidate_privately.py",
            ),
            (
                ROOT / "tools" / "verify_bounded_private_file_replace.py",
                f"{container_tmp}/repo/tools/verify_bounded_private_file_replace.py",
            ),
            (
                Path(__file__).resolve(),
                f"{container_tmp}/repo/tools/run_heater_retire_hardened_dry_run.py",
            ),
            (
                ROOT / LIVE_ONE_RELATIVE,
                f"{container_tmp}/repo/{LIVE_ONE_RELATIVE.as_posix()}",
            ),
            (
                ROOT / LIVE_TWO_RELATIVE,
                f"{container_tmp}/repo/{LIVE_TWO_RELATIVE.as_posix()}",
            ),
        )
        for source, destination in stage_files:
            _copy_into_container(args.docker, args.container, source, destination)

        worker = _run(
            [
                args.docker,
                "exec",
                "-e",
                f"PYTHONPATH={container_tmp}/repo",
                args.container,
                "python",
                f"{container_tmp}/repo/tools/run_heater_retire_hardened_dry_run.py",
                "--container-worker",
                "--config-root",
                "/config",
                "--staged-root",
                f"{container_tmp}/repo",
                "--workspace",
                f"{container_tmp}/workspace",
            ]
        )
        if worker.returncode != 0:
            try:
                decoded = json.loads(worker.stdout)
            except json.JSONDecodeError:
                raise RuntimeError("CONTAINER_WORKER_FAILED") from None
            reason = decoded.get("reason")
            if not isinstance(reason, str):
                raise RuntimeError("CONTAINER_WORKER_FAILED")
            raise RuntimeError(reason)

        try:
            decoded = json.loads(worker.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CONTAINER_WORKER_INVALID_JSON") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("CONTAINER_WORKER_INVALID_REPORT")
        if decoded.get("decision") != WORKER_READY:
            raise RuntimeError("CONTAINER_WORKER_NOT_READY")
        if any(value is not False for value in decoded.get("privacy", {}).values()):
            raise RuntimeError("CONTAINER_WORKER_PRIVACY_REPORT_INVALID")
        if any(
            value is not False
            for value in decoded.get("production_mutation", {}).values()
        ):
            raise RuntimeError("CONTAINER_WORKER_MUTATION_REPORT_INVALID")
        worker_report = decoded
    finally:
        cleanup = _run(
            [args.docker, "exec", args.container, "rm", "-rf", container_tmp]
        )
        cleanup_ok = cleanup.returncode == 0

    if not cleanup_ok:
        return blocked_report("PRIVATE_TEMP_CLEANUP_FAILED")
    if worker_report is None:
        return blocked_report("CONTAINER_WORKER_NO_REPORT")

    running_after = _inspect_value(args.docker, args.container, "{{.State.Running}}")
    started_after = _inspect_value(args.docker, args.container, "{{.State.StartedAt}}")
    restart_after = _inspect_value(args.docker, args.container, "{{.RestartCount}}")
    runtime_unchanged = (
        running_after == "true"
        and hmac.compare_digest(started_before, started_after)
        and hmac.compare_digest(restart_before, restart_after)
    )
    if not runtime_unchanged:
        return blocked_report("HA_RUNTIME_CHANGED")

    return {
        "schema": 1,
        "decision": READY,
        "source_gate": gate,
        "home_assistant": {
            "expected_version_match": True,
            "running_version_match": True,
            "runtime_unchanged": True,
        },
        "live_preconditions_ready": True,
        "worker": worker_report,
        "private_temp_cleanup": True,
        "owner_semantic_choice": "RETIRE",
        "production_apply_authorized": False,
        "scheduler_bootstrap_authorized": False,
        "privacy": privacy_report(),
        "production_mutation": production_mutation_report(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fresh hardened read-only RETIRE dry run for heater #9."
    )
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default="homeassistant")
    parser.add_argument(
        "--expected-version-file", type=Path, default=EXPECTED_VERSION_FILE
    )
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--expected-tree", default="")
    parser.add_argument("--expected-parent", default="")
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retire", action="store_true")
    parser.add_argument("--container-worker", action="store_true")
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--staged-root", type=Path)
    parser.add_argument("--workspace", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.container_worker:
            if any(
                value is None
                for value in (args.config_root, args.staged_root, args.workspace)
            ):
                report = blocked_report("CONTAINER_WORKER_ARGUMENTS_REQUIRED")
            else:
                assert args.config_root is not None
                assert args.staged_root is not None
                assert args.workspace is not None
                report = worker_dry_run(
                    args.config_root, args.staged_root, args.workspace
                )
        else:
            required = (
                args.expected_sha,
                args.expected_tree,
                args.expected_parent,
                args.expected_version,
            )
            if not all(required):
                report = blocked_report("EXACT_SOURCE_AND_VERSION_GATE_REQUIRED")
            else:
                report = host_dry_run(args)
    except Exception as exc:
        reason = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]+", reason):
            reason = "HARDENED_RETIRE_DRY_RUN_RUNTIME_ERROR"
        report = blocked_report(reason)

    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report.get("decision") in {READY, WORKER_READY} else 20


if __name__ == "__main__":
    raise SystemExit(main())
