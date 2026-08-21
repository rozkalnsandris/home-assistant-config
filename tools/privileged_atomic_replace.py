#!/usr/bin/env python3
"""Bounded privileged atomic-replace bridge for heater RETIRE production.

The production gate stays unprivileged. Private target paths and bytes are sent
to this narrowly scoped root worker over stdin only and are never emitted.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
PASS = "PASS"
BLOCKED = "BLOCKED"
APPLY = "apply"
ROLLBACK = "rollback"
EQUALS_CANDIDATE = "equals_candidate"
EQUALS_ORIGINAL = "equals_original"
OTHER = "other"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
PRIVILEGED_SUDO = "/usr/bin/sudo"
PRIVILEGED_PYTHON = "/usr/bin/python3"
ALLOWED_TARGET_NAMES = {1: "silditajs.yaml", 2: "heater_scheduler.yaml"}


class PrivilegedReplacePreflightError(RuntimeError):
    def __init__(self) -> None:
        self.reason = "PRIVILEGED_WRITE_PREFLIGHT_FAILED"
        super().__init__(self.reason)


def privacy_report() -> dict[str, bool]:
    return {
        "candidate_bytes_emitted": False,
        "original_bytes_emitted": False,
        "private_paths_emitted": False,
        "hashes_emitted": False,
        "raw_yaml_emitted": False,
        "secret_values_emitted": False,
    }


def _blocked(
    reason: str,
    *,
    ordinal: int | None = None,
    phase: str | None = None,
    classification: str | None = None,
    changed: bool = False,
    parent_fsync: bool = False,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "decision": BLOCKED,
        "reason": reason,
        "ordinal": ordinal,
        "phase": phase,
        "classification": classification,
        "target_may_have_changed": changed,
        "parent_fsync_performed": parent_fsync,
        "privacy": privacy_report(),
    }


def state_needs_privilege(state: Any) -> bool:
    if os.geteuid() == 0:
        return False
    wrong_owner = (state.target_uid, state.target_gid) != (
        os.geteuid(),
        os.getegid(),
    )
    parent_denied = not os.access(
        state.target_path.parent,
        os.W_OK | os.X_OK,
    )
    return wrong_owner or parent_denied


def _request(state: Any, *, action: str, phase: str, ordinal: int) -> dict[str, Any]:
    encode = lambda value: base64.b64encode(value).decode("ascii")
    return {
        "schema": SCHEMA,
        "action": action,
        "phase": phase,
        "ordinal": ordinal,
        "target_path": str(state.target_path),
        "target_mode": state.target_mode,
        "target_uid": state.target_uid,
        "target_gid": state.target_gid,
        "candidate_b64": encode(state.candidate_bytes),
        "original_b64": encode(state.original_bytes),
    }


def _worktree_clean() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and not result.stdout.strip()


def invoke_privileged_worker(request: dict[str, Any]) -> dict[str, Any]:
    if not _worktree_clean():
        return _blocked(
            "PRIVILEGED_SOURCE_WORKTREE_NOT_CLEAN",
            ordinal=request.get("ordinal"),
            phase=request.get("phase"),
        )
    command = [
        PRIVILEGED_SUDO,
        "-n",
        "--",
        PRIVILEGED_PYTHON,
        "-I",
        "-B",
        str(Path(__file__).resolve()),
        "--worker",
    ]
    try:
        result = subprocess.run(
            command,
            input=json.dumps(request, separators=(",", ":")) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return _blocked(
            "PRIVILEGED_WORKER_EXEC_FAILED",
            ordinal=request.get("ordinal"),
            phase=request.get("phase"),
        )
    if result.returncode not in {0, 20}:
        return _blocked(
            "PRIVILEGED_WORKER_EXEC_FAILED",
            ordinal=request.get("ordinal"),
            phase=request.get("phase"),
        )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        report = None
    if not isinstance(report, dict) or report.get("privacy") != privacy_report():
        return _blocked(
            "PRIVILEGED_WORKER_INVALID_OUTPUT",
            ordinal=request.get("ordinal"),
            phase=request.get("phase"),
        )
    return report


def preflight_privileged_replacements(states: Iterable[Any]) -> None:
    for ordinal, state in enumerate(states, 1):
        if not state_needs_privilege(state):
            continue
        report = invoke_privileged_worker(
            _request(state, action="preflight", phase=APPLY, ordinal=ordinal)
        )
        if report.get("decision") != PASS:
            raise PrivilegedReplacePreflightError()


def _metadata_exact(state: Any) -> bool:
    try:
        info = state.target_path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and stat.S_IMODE(info.st_mode) == state.target_mode
        and info.st_uid == state.target_uid
        and info.st_gid == state.target_gid
    )


def _verified_replace(state: Any, *, phase: str, ordinal: int) -> Any:
    from tools import verify_bounded_private_file_replace as verifier

    direct = (
        verifier.apply_verified_replace
        if phase == APPLY
        else verifier.rollback_verified_replace
    )
    if not state_needs_privilege(state):
        return direct(state, ordinal=ordinal)

    report = invoke_privileged_worker(
        _request(state, action="replace", phase=phase, ordinal=ordinal)
    )
    try:
        actual = state.target_path.read_bytes()
        classification = verifier.classify_bytes(
            actual,
            state.candidate_bytes,
            state.original_bytes,
        )
    except OSError:
        classification = None

    if report.get("decision") != PASS:
        reason = (
            "ATOMIC_APPLY_FAILED"
            if phase == APPLY
            else "ATOMIC_ROLLBACK_FAILED"
        )
        if phase == APPLY and report.get("reason") in {
            "TARGET_METADATA_DRIFTED",
            "TARGET_CONTENT_DRIFTED_BEFORE_APPLY",
        }:
            reason = str(report["reason"])
        raise verifier.VerifiedReplaceError(
            reason,
            ordinal=ordinal,
            phase=phase,
            classification=classification,
            target_may_have_changed=(
                classification != verifier.EQUALS_ORIGINAL
                if phase == APPLY
                else True
            ),
            parent_fsync_performed=(
                report.get("parent_fsync_performed") is True
            ),
        )

    expected = (
        verifier.EQUALS_CANDIDATE
        if phase == APPLY
        else verifier.EQUALS_ORIGINAL
    )
    if (
        classification != expected
        or not _metadata_exact(state)
        or report.get("classification") != expected
        or report.get("parent_fsync_performed") is not True
    ):
        raise verifier.VerifiedReplaceError(
            (
                "POST_APPLY_VERIFICATION_MISMATCH"
                if phase == APPLY
                else "POST_ROLLBACK_VERIFICATION_MISMATCH"
            ),
            ordinal=ordinal,
            phase=phase,
            classification=classification,
            target_may_have_changed=True,
            parent_fsync_performed=(
                report.get("parent_fsync_performed") is True
            ),
        )
    return verifier.VerificationResult(
        ordinal=ordinal,
        phase=phase,
        classification=expected,
        parent_fsync_performed=True,
    )


def apply_verified_replace_with_privilege(state: Any, *, ordinal: int) -> Any:
    return _verified_replace(state, phase=APPLY, ordinal=ordinal)


def rollback_verified_replace_with_privilege(state: Any, *, ordinal: int) -> Any:
    return _verified_replace(state, phase=ROLLBACK, ordinal=ordinal)


def _decode(payload: dict[str, Any], field: str) -> bytes:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError
    return base64.b64decode(value.encode("ascii"), validate=True)


def _target_request(
    payload: dict[str, Any],
) -> tuple[Path, int, int, int, int, str, str]:
    if payload.get("schema") != SCHEMA:
        raise ValueError
    action = payload.get("action")
    phase = payload.get("phase")
    ordinal = payload.get("ordinal")
    raw = payload.get("target_path")
    mode = payload.get("target_mode")
    uid = payload.get("target_uid")
    gid = payload.get("target_gid")
    if action not in {"preflight", "replace"} or phase not in {APPLY, ROLLBACK}:
        raise ValueError
    if not isinstance(ordinal, int) or ordinal not in ALLOWED_TARGET_NAMES:
        raise ValueError
    if not isinstance(raw, str) or not isinstance(mode, int):
        raise ValueError
    if not isinstance(uid, int) or uid < 0 or not isinstance(gid, int) or gid < 0:
        raise ValueError
    if not 0 <= mode <= 0o7777:
        raise ValueError
    target = Path(raw)
    if (
        not target.is_absolute()
        or target.name != ALLOWED_TARGET_NAMES[ordinal]
        or target.parent.name != "packages"
    ):
        raise ValueError
    return target, mode, uid, gid, ordinal, str(phase), str(action)


def _regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError
    return info


def _directory(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError


def _classify(actual: bytes, candidate: bytes, original: bytes) -> str:
    if hmac.compare_digest(actual, candidate):
        return EQUALS_CANDIDATE
    if hmac.compare_digest(actual, original):
        return EQUALS_ORIGINAL
    return OTHER


def _metadata(info: os.stat_result, mode: int, uid: int, gid: int) -> bool:
    return (
        stat.S_IMODE(info.st_mode) == mode
        and info.st_uid == uid
        and info.st_gid == gid
    )


def _parent_writable(parent: Path) -> bool:
    flags = os.statvfs(parent).f_flag
    return not (flags & getattr(os, "ST_RDONLY", 1)) and os.access(
        parent,
        os.W_OK | os.X_OK,
        effective_ids=True,
    )


def _fsync_parent(parent: Path) -> None:
    fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_replace(
    target: Path,
    content: bytes,
    mode: int,
    uid: int,
    gid: int,
    label: str,
) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.{label}.", dir=target.parent)
    temp = Path(name)
    replaced = False
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        replaced = True
        _fsync_parent(target.parent)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if not replaced:
            try:
                temp.unlink()
            except OSError:
                pass


def worker_handle(payload: dict[str, Any]) -> dict[str, Any]:
    ordinal = None
    phase = None
    try:
        if os.geteuid() != 0:
            return _blocked("PRIVILEGED_WORKER_REQUIRES_ROOT")
        target, mode, uid, gid, ordinal, phase, action = _target_request(payload)
        candidate = _decode(payload, "candidate_b64")
        original = _decode(payload, "original_b64")
        info = _regular(target)
        _directory(target.parent)

        if action == "preflight":
            if (
                phase != APPLY
                or not _metadata(info, mode, uid, gid)
                or _classify(target.read_bytes(), candidate, original)
                != EQUALS_ORIGINAL
                or not _parent_writable(target.parent)
            ):
                return _blocked(
                    "PRIVILEGED_WRITE_PREFLIGHT_FAILED",
                    ordinal=ordinal,
                    phase=phase,
                )
            return {
                "schema": SCHEMA,
                "decision": PASS,
                "reason": None,
                "ordinal": ordinal,
                "phase": phase,
                "classification": EQUALS_ORIGINAL,
                "target_may_have_changed": False,
                "parent_fsync_performed": False,
                "privacy": privacy_report(),
            }

        if phase == APPLY:
            current = _classify(target.read_bytes(), candidate, original)
            if not _metadata(info, mode, uid, gid):
                return _blocked("TARGET_METADATA_DRIFTED", ordinal=ordinal, phase=phase)
            if current != EQUALS_ORIGINAL:
                return _blocked(
                    "TARGET_CONTENT_DRIFTED_BEFORE_APPLY",
                    ordinal=ordinal,
                    phase=phase,
                    classification=current,
                )

        content = candidate if phase == APPLY else original
        try:
            _atomic_replace(target, content, mode, uid, gid, f"{phase}-{ordinal}")
        except Exception:
            try:
                current = _classify(target.read_bytes(), candidate, original)
            except OSError:
                current = None
            return _blocked(
                "ATOMIC_APPLY_FAILED" if phase == APPLY else "ATOMIC_ROLLBACK_FAILED",
                ordinal=ordinal,
                phase=phase,
                classification=current,
                changed=(current != EQUALS_ORIGINAL if phase == APPLY else True),
            )

        current = _classify(target.read_bytes(), candidate, original)
        final = _regular(target)
        expected = EQUALS_CANDIDATE if phase == APPLY else EQUALS_ORIGINAL
        if current != expected or not _metadata(final, mode, uid, gid):
            return _blocked(
                (
                    "POST_APPLY_VERIFICATION_MISMATCH"
                    if phase == APPLY
                    else "POST_ROLLBACK_VERIFICATION_MISMATCH"
                ),
                ordinal=ordinal,
                phase=phase,
                classification=current,
                changed=True,
                parent_fsync=True,
            )
        return {
            "schema": SCHEMA,
            "decision": PASS,
            "reason": None,
            "ordinal": ordinal,
            "phase": phase,
            "classification": expected,
            "target_may_have_changed": phase == APPLY,
            "parent_fsync_performed": True,
            "privacy": privacy_report(),
        }
    except Exception:
        return _blocked(
            "PRIVILEGED_WORKER_REQUEST_FAILED",
            ordinal=ordinal,
            phase=phase,
        )


def _worker_main() -> int:
    try:
        raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        report = worker_handle(payload)
    except Exception:
        report = _blocked("PRIVILEGED_WORKER_REQUEST_FAILED")
    sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    return 0 if report.get("decision") == PASS else 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args(argv)
    if not args.worker:
        sys.stdout.write(
            json.dumps(_blocked("EXPLICIT_PRIVILEGED_WORKER_MODE_REQUIRED"))
            + "\n"
        )
        return 20
    return _worker_main()


if __name__ == "__main__":
    raise SystemExit(main())
