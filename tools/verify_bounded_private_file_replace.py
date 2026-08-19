#!/usr/bin/env python3
"""Privacy-safe verified atomic replacement primitives for bounded live files."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterable

EQUALS_CANDIDATE = "equals_candidate"
EQUALS_ORIGINAL = "equals_original"
OTHER = "other"
CLASSIFICATIONS = {EQUALS_CANDIDATE, EQUALS_ORIGINAL, OTHER}

APPLY = "apply"
ROLLBACK = "rollback"


@dataclass(frozen=True, repr=False)
class PrivateFileState:
    """Private in-memory state required for verified apply and rollback."""

    candidate_path: Path
    target_path: Path
    candidate_bytes: bytes
    original_bytes: bytes
    target_mode: int
    target_uid: int
    target_gid: int


@dataclass(frozen=True)
class VerificationResult:
    """Privacy-safe result for one bounded ordinal."""

    ordinal: int
    phase: str
    classification: str
    parent_fsync_performed: bool

    def public_report(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "decision": "VERIFIED",
            "ordinal": self.ordinal,
            "phase": self.phase,
            "classification": self.classification,
            "parent_fsync_performed": self.parent_fsync_performed,
            "privacy": privacy_report(),
        }


class VerifiedReplaceError(RuntimeError):
    """Fail-closed error carrying only public-safe write state."""

    def __init__(
        self,
        reason: str,
        *,
        ordinal: int,
        phase: str,
        classification: str | None = None,
        target_may_have_changed: bool = False,
        parent_fsync_performed: bool = False,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.ordinal = ordinal
        self.phase = phase
        self.classification = classification
        self.target_may_have_changed = target_may_have_changed
        self.parent_fsync_performed = parent_fsync_performed

    def public_report(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "decision": "BLOCKED",
            "reason": self.reason,
            "ordinal": self.ordinal,
            "phase": self.phase,
            "classification": self.classification,
            "target_may_have_changed": self.target_may_have_changed,
            "parent_fsync_performed": self.parent_fsync_performed,
            "privacy": privacy_report(),
        }


def privacy_report() -> dict[str, bool]:
    return {
        "candidate_bytes_emitted": False,
        "original_bytes_emitted": False,
        "hashes_emitted": False,
        "private_paths_emitted": False,
        "entity_ids_or_targets_emitted": False,
        "secret_aliases_or_values_emitted": False,
        "raw_yaml_emitted": False,
    }


def _require_regular_non_symlink(path: Path, reason: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(reason) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(reason)
    return info


def _require_directory_non_symlink(path: Path, reason: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(reason) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(reason)
    return info


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read()


def classify_bytes(actual: bytes, candidate: bytes, original: bytes) -> str:
    if hmac.compare_digest(actual, candidate):
        return EQUALS_CANDIDATE
    if hmac.compare_digest(actual, original):
        return EQUALS_ORIGINAL
    return OTHER


def snapshot_private_file(candidate_path: Path, target_path: Path) -> PrivateFileState:
    candidate = Path(candidate_path)
    target = Path(target_path)

    if candidate == target:
        raise ValueError("CANDIDATE_AND_TARGET_MUST_DIFFER")

    _require_regular_non_symlink(candidate, "CANDIDATE_FILE_INVALID")
    target_info = _require_regular_non_symlink(target, "TARGET_FILE_INVALID")
    _require_directory_non_symlink(target.parent, "TARGET_PARENT_DIRECTORY_INVALID")

    return PrivateFileState(
        candidate_path=candidate,
        target_path=target,
        candidate_bytes=_read_bytes(candidate),
        original_bytes=_read_bytes(target),
        target_mode=stat.S_IMODE(target_info.st_mode),
        target_uid=target_info.st_uid,
        target_gid=target_info.st_gid,
    )


def _target_metadata_matches_snapshot(state: PrivateFileState) -> bool:
    try:
        info = _require_regular_non_symlink(
            state.target_path,
            "TARGET_FILE_INVALID",
        )
    except ValueError:
        return False
    return (
        stat.S_IMODE(info.st_mode) == state.target_mode
        and info.st_uid == state.target_uid
        and info.st_gid == state.target_gid
    )


def _fsync_parent_directory(parent: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(parent, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_replace_bytes(
    state: PrivateFileState,
    content: bytes,
    *,
    temp_label: str,
) -> bool:
    """Replace target atomically and return only after parent directory fsync."""

    target = state.target_path
    _require_regular_non_symlink(target, "TARGET_FILE_INVALID")
    _require_directory_non_symlink(target.parent, "TARGET_PARENT_DIRECTORY_INVALID")

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.{temp_label}.",
        dir=str(target.parent),
    )
    temp_path = Path(temp_name)
    replaced = False

    try:
        os.fchmod(fd, state.target_mode)

        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        if os.geteuid() == 0:
            os.chown(temp_path, state.target_uid, state.target_gid)
        elif (state.target_uid, state.target_gid) != (os.geteuid(), os.getegid()):
            raise PermissionError("cannot preserve target ownership")

        os.replace(temp_path, target)
        replaced = True
        _fsync_parent_directory(target.parent)
        return True
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if not replaced:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _verify_expected_original_before_apply(
    state: PrivateFileState,
    *,
    ordinal: int,
) -> None:
    if not _target_metadata_matches_snapshot(state):
        raise VerifiedReplaceError(
            "TARGET_METADATA_DRIFTED",
            ordinal=ordinal,
            phase=APPLY,
        )

    try:
        actual = _read_bytes(state.target_path)
    except OSError as exc:
        raise VerifiedReplaceError(
            "TARGET_READ_FAILED_BEFORE_APPLY",
            ordinal=ordinal,
            phase=APPLY,
        ) from exc

    classification = classify_bytes(
        actual,
        state.candidate_bytes,
        state.original_bytes,
    )
    if classification != EQUALS_ORIGINAL:
        raise VerifiedReplaceError(
            "TARGET_CONTENT_DRIFTED_BEFORE_APPLY",
            ordinal=ordinal,
            phase=APPLY,
            classification=classification,
        )


def apply_verified_replace(
    state: PrivateFileState,
    *,
    ordinal: int,
) -> VerificationResult:
    _verify_expected_original_before_apply(state, ordinal=ordinal)

    try:
        _atomic_replace_bytes(
            state,
            state.candidate_bytes,
            temp_label=f"apply-{ordinal}",
        )
    except Exception as exc:
        classification = None
        try:
            classification = classify_bytes(
                _read_bytes(state.target_path),
                state.candidate_bytes,
                state.original_bytes,
            )
        except OSError:
            pass
        raise VerifiedReplaceError(
            "ATOMIC_APPLY_FAILED",
            ordinal=ordinal,
            phase=APPLY,
            classification=classification,
            target_may_have_changed=classification != EQUALS_ORIGINAL,
            parent_fsync_performed=False,
        ) from exc

    try:
        actual = _read_bytes(state.target_path)
    except OSError as exc:
        raise VerifiedReplaceError(
            "TARGET_READ_FAILED_AFTER_APPLY",
            ordinal=ordinal,
            phase=APPLY,
            target_may_have_changed=True,
            parent_fsync_performed=True,
        ) from exc

    classification = classify_bytes(
        actual,
        state.candidate_bytes,
        state.original_bytes,
    )

    if classification != EQUALS_CANDIDATE:
        raise VerifiedReplaceError(
            "POST_APPLY_VERIFICATION_MISMATCH",
            ordinal=ordinal,
            phase=APPLY,
            classification=classification,
            target_may_have_changed=True,
            parent_fsync_performed=True,
        )

    return VerificationResult(
        ordinal=ordinal,
        phase=APPLY,
        classification=classification,
        parent_fsync_performed=True,
    )


def rollback_verified_replace(
    state: PrivateFileState,
    *,
    ordinal: int,
) -> VerificationResult:
    try:
        _require_regular_non_symlink(state.target_path, "TARGET_FILE_INVALID")
        _atomic_replace_bytes(
            state,
            state.original_bytes,
            temp_label=f"rollback-{ordinal}",
        )
    except Exception as exc:
        classification = None
        try:
            classification = classify_bytes(
                _read_bytes(state.target_path),
                state.candidate_bytes,
                state.original_bytes,
            )
        except OSError:
            pass
        raise VerifiedReplaceError(
            "ATOMIC_ROLLBACK_FAILED",
            ordinal=ordinal,
            phase=ROLLBACK,
            classification=classification,
            target_may_have_changed=True,
            parent_fsync_performed=False,
        ) from exc

    try:
        actual = _read_bytes(state.target_path)
    except OSError as exc:
        raise VerifiedReplaceError(
            "TARGET_READ_FAILED_AFTER_ROLLBACK",
            ordinal=ordinal,
            phase=ROLLBACK,
            target_may_have_changed=True,
            parent_fsync_performed=True,
        ) from exc

    classification = classify_bytes(
        actual,
        state.candidate_bytes,
        state.original_bytes,
    )

    if classification != EQUALS_ORIGINAL:
        raise VerifiedReplaceError(
            "POST_ROLLBACK_VERIFICATION_MISMATCH",
            ordinal=ordinal,
            phase=ROLLBACK,
            classification=classification,
            target_may_have_changed=True,
            parent_fsync_performed=True,
        )

    return VerificationResult(
        ordinal=ordinal,
        phase=ROLLBACK,
        classification=classification,
        parent_fsync_performed=True,
    )


def apply_verified_sequence(
    states: Iterable[PrivateFileState],
) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    for ordinal, state in enumerate(states, start=1):
        results.append(
            apply_verified_replace(
                state,
                ordinal=ordinal,
            )
        )
    return results
