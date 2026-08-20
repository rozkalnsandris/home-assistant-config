#!/usr/bin/env python3
"""Common fail-closed primitives for the heater RETIRE production gate."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_heater_scheduling_live import collect_runtime_probe

EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"

GATE_ID = "heater-retire-123-v1"
AUTHORIZATION_PREFIX = "Authorize heater RETIRE production apply"
BLOCKED = "BLOCKED"

AUTH_MARKER_BASE = (
    Path.home()
    / ".local"
    / "share"
    / "ha-production-authorizations"
    / "heater-retire"
)
ROLLBACK_BASE = (
    Path.home()
    / ".local"
    / "share"
    / "ha-private-rollbacks"
    / "heater-retire-production"
)

MAX_SHA_LENGTH = 40
RESTART_ATTEMPTS = 40
POLL_SECONDS = 1.0


class GateError(RuntimeError):
    """Public-safe production-gate error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def privacy_report() -> dict[str, bool]:
    return {
        "config_contents_emitted": False,
        "private_paths_emitted": False,
        "entity_ids_or_targets_emitted": False,
        "secret_aliases_emitted": False,
        "secret_values_emitted": False,
        "schedule_names_emitted": False,
        "schedule_times_emitted": False,
        "weekdays_or_dates_emitted": False,
        "private_hashes_emitted": False,
    }


def mutation_report(
    *,
    config_written: bool,
    restart_attempted: bool,
) -> dict[str, bool]:
    return {
        "home_assistant_config_written": config_written,
        "scheduler_service_called": False,
        "scheduler_storage_written": False,
        "helper_state_changed_directly": False,
        "automation_state_changed_directly": False,
        "heater_actuated_directly": False,
        "reload_performed": False,
        "restart_attempted": restart_attempted,
    }


def all_false(section: Any) -> bool:
    return isinstance(section, dict) and all(value is False for value in section.values())


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git_value(args: list[str]) -> str:
    result = run(["git", *args], cwd=ROOT)
    if result.returncode != 0:
        raise GateError("SOURCE_GIT_PROBE_FAILED")
    value = result.stdout.strip()
    if not value:
        raise GateError("SOURCE_GIT_PROBE_FAILED")
    return value


def validate_object_id(value: str, reason: str) -> None:
    if len(value) != MAX_SHA_LENGTH:
        raise GateError(reason)
    if any(char not in "0123456789abcdef" for char in value):
        raise GateError(reason)


def source_gate(
    expected_sha: str,
    expected_tree: str,
    expected_parent: str,
) -> dict[str, bool]:
    validate_object_id(expected_sha, "EXPECTED_SHA_INVALID")
    validate_object_id(expected_tree, "EXPECTED_TREE_INVALID")
    validate_object_id(expected_parent, "EXPECTED_PARENT_INVALID")
    return {
        "sha_match": hmac.compare_digest(
            _git_value(["rev-parse", "HEAD"]),
            expected_sha,
        ),
        "tree_match": hmac.compare_digest(
            _git_value(["rev-parse", "HEAD^{tree}"]),
            expected_tree,
        ),
        "parent_match": hmac.compare_digest(
            _git_value(["rev-parse", "HEAD^"]),
            expected_parent,
        ),
    }


def expected_authorization(expected_sha: str) -> str:
    validate_object_id(expected_sha, "EXPECTED_SHA_INVALID")
    return f"{AUTHORIZATION_PREFIX} {expected_sha}-{GATE_ID}"


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise GateError("PRIVATE_STATE_DIRECTORY_INVALID")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, 0o700)
    fsync_directory(path.parent)


def authorization_marker_path(marker_base: Path, expected_sha: str) -> Path:
    validate_object_id(expected_sha, "EXPECTED_SHA_INVALID")
    return marker_base / f"{GATE_ID}-{expected_sha}.consumed"


def authorization_marker_consumed(
    marker_base: Path,
    expected_sha: str,
) -> bool:
    marker = authorization_marker_path(marker_base, expected_sha)
    try:
        marker.lstat()
    except OSError:
        return False
    return True


def authorization_precheck(
    *,
    marker_base: Path,
    expected_sha: str,
    authorization: str,
) -> None:
    expected = expected_authorization(expected_sha)
    if not hmac.compare_digest(authorization, expected):
        raise GateError("EXACT_ONE_SHOT_AUTHORIZATION_REQUIRED")
    if authorization_marker_consumed(marker_base, expected_sha):
        raise GateError("AUTHORIZATION_ALREADY_CONSUMED")


def consume_authorization(
    *,
    marker_base: Path,
    expected_sha: str,
    authorization: str,
) -> None:
    authorization_precheck(
        marker_base=marker_base,
        expected_sha=expected_sha,
        authorization=authorization,
    )
    ensure_private_directory(marker_base)
    marker = authorization_marker_path(marker_base, expected_sha)
    payload = (
        json.dumps(
            {
                "schema": 1,
                "gate": GATE_ID,
                "source_sha": expected_sha,
                "consumed": True,
            },
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise GateError("AUTHORIZATION_ALREADY_CONSUMED") from exc
    except OSError as exc:
        raise GateError("AUTHORIZATION_MARKER_CREATE_FAILED") from exc

    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    fsync_directory(marker_base)


def expected_version_from_source() -> str:
    try:
        value = EXPECTED_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise GateError("EXPECTED_VERSION_FILE_UNAVAILABLE") from exc
    if not value:
        raise GateError("EXPECTED_VERSION_INVALID")
    return value


def running_version(docker: str, container: str) -> str:
    result = run(
        [
            docker,
            "exec",
            container,
            "python",
            "-m",
            "homeassistant",
            "--version",
        ]
    )
    if result.returncode != 0:
        raise GateError("HA_VERSION_PROBE_FAILED")
    value = result.stdout.strip()
    if not value:
        raise GateError("HA_VERSION_PROBE_FAILED")
    return value


def container_started_at(docker: str, container: str) -> str:
    result = run(
        [docker, "inspect", "-f", "{{.State.StartedAt}}", container]
    )
    if result.returncode != 0:
        raise GateError("HA_CONTAINER_INSPECT_FAILED")
    value = result.stdout.strip()
    if not value:
        raise GateError("HA_CONTAINER_INSPECT_FAILED")
    return value


def container_running(docker: str, container: str) -> bool:
    result = run(
        [docker, "inspect", "-f", "{{.State.Running}}", container]
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def ha_http_ready(docker: str, container: str) -> bool:
    probe = (
        "import socket\n"
        "s=socket.socket(); s.settimeout(1.0)\n"
        "try:\n"
        " s.connect(('127.0.0.1',8123)); print('ready')\n"
        "except OSError:\n"
        " print('not-ready')\n"
        "finally:\n"
        " s.close()\n"
    )
    result = run([docker, "exec", container, "python", "-c", probe])
    return result.returncode == 0 and result.stdout.strip() == "ready"


def restart_and_wait(
    *,
    docker: str,
    container: str,
    expected_version: str,
    before_started_at: str,
) -> dict[str, bool]:
    result = run([docker, "restart", container])
    if result.returncode != 0:
        raise GateError("HOME_ASSISTANT_RESTART_FAILED")

    for _attempt in range(RESTART_ATTEMPTS):
        if not container_running(docker, container):
            time.sleep(POLL_SECONDS)
            continue
        try:
            started_at = container_started_at(docker, container)
            version = running_version(docker, container)
        except GateError:
            time.sleep(POLL_SECONDS)
            continue
        if (
            started_at != before_started_at
            and version == expected_version
            and ha_http_ready(docker, container)
        ):
            return {
                "restart_boundary_changed": True,
                "running_version_match": True,
                "http_listener_ready": True,
            }
        time.sleep(POLL_SECONDS)
    raise GateError("HOME_ASSISTANT_POST_RESTART_NOT_READY")


def discover_config_root(docker: str, container: str) -> Path:
    result = run([docker, "inspect", container])
    if result.returncode != 0:
        raise GateError("HA_CONTAINER_INSPECT_FAILED")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateError("HA_CONTAINER_INSPECT_INVALID_JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1:
        raise GateError("HA_CONTAINER_INSPECT_SHAPE_INVALID")
    item = payload[0]
    mounts = item.get("Mounts") if isinstance(item, dict) else None
    if not isinstance(mounts, list):
        raise GateError("HA_CONFIG_MOUNT_NOT_FOUND")
    matches = [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and mount.get("Destination") == "/config"
        and isinstance(mount.get("Source"), str)
    ]
    if len(matches) != 1:
        raise GateError("HA_CONFIG_MOUNT_NOT_UNIQUE")

    root = Path(matches[0]["Source"])
    if not root.is_absolute():
        raise GateError("HA_CONFIG_MOUNT_SOURCE_INVALID")
    try:
        info = root.lstat()
    except OSError as exc:
        raise GateError("HA_CONFIG_MOUNT_SOURCE_INVALID") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise GateError("HA_CONFIG_MOUNT_SOURCE_INVALID")
    return root


def check_live_config(docker: str, container: str) -> bool:
    result = run(
        [
            docker,
            "exec",
            container,
            "python",
            "-m",
            "homeassistant",
            "--script",
            "check_config",
            "--config",
            "/config",
            "--fail-on-warnings",
        ]
    )
    return result.returncode == 0


def runtime_safety(
    states: Any,
    *,
    expected_timer_state: str | None,
    require_legacy_count: bool,
) -> dict[str, Any]:
    if not isinstance(states, dict):
        raise GateError("RUNTIME_STATE_PROBE_INVALID")
    if states.get("recorder_available") is not True:
        raise GateError("RECORDER_STATE_UNAVAILABLE")

    helper_state = states.get("legacy_schedule_helper_state")
    if helper_state not in {"off", "unknown", "unavailable"}:
        raise GateError("LEGACY_SCHEDULE_HELPER_EXPLICITLY_ON_OR_INVALID")
    if states.get("legacy_automation_enabled_count") != 0:
        raise GateError("LEGACY_AUTOMATION_EXPLICITLY_ENABLED")
    if require_legacy_count and states.get("legacy_automation_count") != 2:
        raise GateError("LEGACY_AUTOMATIONS_NOT_EXACT")

    timer_state = states.get("timer_helper_state")
    if timer_state not in {"on", "off"}:
        raise GateError("TIMER_HELPER_STATE_NOT_OBSERVABLE")
    if expected_timer_state is not None and timer_state != expected_timer_state:
        raise GateError("TIMER_HELPER_STATE_DRIFTED")

    return {
        "recorder_available": True,
        "legacy_schedule_helper_not_on": True,
        "legacy_automation_enabled_count": 0,
        "legacy_automation_count": states.get("legacy_automation_count"),
        "timer_helper_state_observable": True,
        "timer_helper_state_matches_prewrite": (
            expected_timer_state is None or timer_state == expected_timer_state
        ),
        "full_state_machine_equivalence_claimed": False,
    }


def collect_runtime_safety(
    *,
    docker: str,
    container: str,
    expected_timer_state: str | None,
    require_legacy_count: bool,
) -> tuple[dict[str, Any], str]:
    probe = collect_runtime_probe(docker, container)
    states = probe.get("states") if isinstance(probe, dict) else None
    report = runtime_safety(
        states,
        expected_timer_state=expected_timer_state,
        require_legacy_count=require_legacy_count,
    )
    if not isinstance(states, dict):
        raise GateError("RUNTIME_STATE_PROBE_INVALID")
    timer_state = states.get("timer_helper_state")
    if not isinstance(timer_state, str):
        raise GateError("TIMER_HELPER_STATE_NOT_OBSERVABLE")
    return report, timer_state
