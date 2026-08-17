#!/usr/bin/env python3
"""Audit entity-reference coverage across bounded Home Assistant config surfaces."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any

from tools.audit_majas_content_cleanup import (
    DEFAULT_CONFIG_ROOT,
    DEFAULT_DASHBOARD_TITLE,
    EXPECTED_HA_VERSION,
    ContentCleanupAuditError,
    build_live_report as build_phase4a_report,
)
from tools.materialize_majas_dashboard_candidate import CandidateError
from tools.plan_majas_dashboard_activation import (
    ActivationPlanError,
    resolve_binding_owner,
)

READY_DECISION = "READY_FOR_PRIVATE_UNUSED_CANDIDATE_EVIDENCE_REVIEW"
NO_CANDIDATES_DECISION = "NO_UNREFERENCED_CORPUS_CANDIDATES"

REGISTRY_RELATIVE_PATH = Path(".storage") / "core.entity_registry"
ROOT_SURFACES = {
    "configuration": "configuration.yaml",
    "automations": "automations.yaml",
    "scripts": "scripts.yaml",
    "scenes": "scenes.yaml",
}
RECURSIVE_SURFACES = {
    "packages": "packages",
    "templates": "templates",
    "blueprints": "blueprints",
}

MAX_SOURCE_FILE_COUNT = 256
MAX_SOURCE_DIRECTORY_COUNT = 256
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_REGISTRY_BYTES = 8 * 1024 * 1024
MAX_REGISTRY_ENTITIES = 10000

ENTITY_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])([a-z0-9_]+\.[a-z0-9_]+)(?![A-Za-z0-9_])"
)


class CrossConfigReferenceAuditError(RuntimeError):
    """Sanitized Phase 4C audit failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "binding_values_emitted": False,
        "entity_ids_emitted": False,
        "entity_like_values_emitted": False,
        "registry_payload_emitted": False,
        "registry_entity_ids_emitted": False,
        "registry_platform_names_emitted": False,
        "friendly_names_emitted": False,
        "unique_ids_emitted": False,
        "device_or_config_entry_ids_emitted": False,
        "secret_values_emitted": False,
    }


def mutation_report() -> dict[str, bool]:
    return {
        "owner_file_modified": False,
        "dashboard_modified": False,
        "config_yaml_modified": False,
        "automation_script_scene_modified": False,
        "storage_write": False,
        "registry_modified": False,
        "helper_or_entity_removed": False,
        "binding_changed": False,
        "grid_options_modified": False,
        "old_source_modified": False,
        "reload_or_restart": False,
    }


def claim_report() -> dict[str, bool]:
    return {
        "unused_claimed": False,
        "safe_to_remove_claimed": False,
        "automatic_removal_authorized": False,
    }


def blocked_report(reason: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": [reason],
        "claims": claim_report(),
        "privacy": privacy_report(),
        "mutation": mutation_report(),
    }


def _require_regular_file(path: Path, reason: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise CrossConfigReferenceAuditError(reason)


def _walk_yaml_files(root: Path) -> tuple[list[Path], int]:
    """Return regular YAML files without following symlinks."""

    if root.is_symlink():
        raise CrossConfigReferenceAuditError("RECURSIVE_SOURCE_SYMLINK_PRESENT")
    if not root.exists():
        return [], 0
    if not root.is_dir():
        raise CrossConfigReferenceAuditError("RECURSIVE_SOURCE_NOT_DIRECTORY")

    files: list[Path] = []
    directory_count = 0
    stack = [root]

    while stack:
        current = stack.pop()
        directory_count += 1
        if directory_count > MAX_SOURCE_DIRECTORY_COUNT:
            raise CrossConfigReferenceAuditError("SOURCE_DIRECTORY_COUNT_LIMIT_EXCEEDED")

        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise CrossConfigReferenceAuditError("SOURCE_DIRECTORY_READ_FAILED") from exc

        for entry in entries:
            if entry.is_symlink():
                raise CrossConfigReferenceAuditError("RECURSIVE_SOURCE_SYMLINK_PRESENT")
            if entry.is_dir():
                stack.append(entry)
                continue
            if not entry.is_file():
                raise CrossConfigReferenceAuditError("SOURCE_UNEXPECTED_ENTRY")
            if entry.suffix.lower() not in {".yaml", ".yml"}:
                continue
            files.append(entry)
            if len(files) > MAX_SOURCE_FILE_COUNT:
                raise CrossConfigReferenceAuditError("SOURCE_FILE_COUNT_LIMIT_EXCEEDED")

    files.sort(key=lambda item: item.as_posix())
    return files, directory_count


def _read_bounded_text(path: Path) -> tuple[str, int]:
    _require_regular_file(path, "SOURCE_FILE_NOT_REGULAR")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CrossConfigReferenceAuditError("SOURCE_FILE_STAT_FAILED") from exc
    if size > MAX_SOURCE_FILE_BYTES:
        raise CrossConfigReferenceAuditError("SOURCE_FILE_SIZE_LIMIT_EXCEEDED")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CrossConfigReferenceAuditError("SOURCE_FILE_READ_FAILED") from exc
    if len(data) != size:
        raise CrossConfigReferenceAuditError("SOURCE_FILE_SIZE_DRIFT")
    return data.decode("utf-8", errors="replace"), size


def _collect_source_files(
    config_root: Path,
    active_root: Path,
) -> tuple[dict[str, list[Path]], int, int]:
    surfaces: dict[str, list[Path]] = {"dashboard": []}

    dashboard_files = sorted(
        (path for path in active_root.rglob("*") if path.is_file()),
        key=lambda item: item.relative_to(active_root).as_posix(),
    )
    if any(path.is_symlink() for path in active_root.rglob("*")):
        raise CrossConfigReferenceAuditError("ACTIVE_DASHBOARD_SYMLINK_PRESENT")
    surfaces["dashboard"] = dashboard_files

    for surface, filename in ROOT_SURFACES.items():
        path = config_root / filename
        if path.exists() or path.is_symlink():
            _require_regular_file(path, "ROOT_SOURCE_NOT_REGULAR")
            surfaces[surface] = [path]
        else:
            surfaces[surface] = []

    if not surfaces["configuration"]:
        raise CrossConfigReferenceAuditError("CONFIGURATION_SOURCE_UNAVAILABLE")

    recursive_directory_count = 0
    for surface, dirname in RECURSIVE_SURFACES.items():
        files, dirs = _walk_yaml_files(config_root / dirname)
        surfaces[surface] = files
        recursive_directory_count += dirs
        if recursive_directory_count > MAX_SOURCE_DIRECTORY_COUNT:
            raise CrossConfigReferenceAuditError("SOURCE_DIRECTORY_COUNT_LIMIT_EXCEEDED")

    all_paths: list[Path] = []
    seen: set[Path] = set()
    for paths in surfaces.values():
        for path in paths:
            resolved = path.resolve(strict=True)
            if resolved in seen:
                continue
            seen.add(resolved)
            all_paths.append(path)

    if len(all_paths) > MAX_SOURCE_FILE_COUNT:
        raise CrossConfigReferenceAuditError("SOURCE_FILE_COUNT_LIMIT_EXCEEDED")

    total_bytes = 0
    for path in all_paths:
        _text, size = _read_bounded_text(path)
        total_bytes += size
        if total_bytes > MAX_SOURCE_TOTAL_BYTES:
            raise CrossConfigReferenceAuditError("SOURCE_TOTAL_SIZE_LIMIT_EXCEEDED")

    return surfaces, len(all_paths), total_bytes


def _read_registry_candidates(config_root: Path) -> set[str]:
    registry = config_root / REGISTRY_RELATIVE_PATH
    _require_regular_file(registry, "ENTITY_REGISTRY_UNAVAILABLE")

    try:
        size = registry.stat().st_size
    except OSError as exc:
        raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_STAT_FAILED") from exc
    if size > MAX_REGISTRY_BYTES:
        raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_SIZE_LIMIT_EXCEEDED")

    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_PARSE_FAILED") from exc

    if not isinstance(payload, dict):
        raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_FORMAT_DRIFT")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_FORMAT_DRIFT")
    entities = data.get("entities")
    if not isinstance(entities, list):
        raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_FORMAT_DRIFT")
    if len(entities) > MAX_REGISTRY_ENTITIES:
        raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_COUNT_LIMIT_EXCEEDED")

    candidates: set[str] = set()
    for entry in entities:
        if not isinstance(entry, dict):
            raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_FORMAT_DRIFT")
        entity_id = entry.get("entity_id")
        if not isinstance(entity_id, str) or ENTITY_TOKEN_RE.fullmatch(entity_id) is None:
            raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_FORMAT_DRIFT")
        if entity_id in candidates:
            raise CrossConfigReferenceAuditError("ENTITY_REGISTRY_DUPLICATE_ENTITY_ID")
        candidates.add(entity_id)

    return candidates


def _surface_candidate_references(
    surfaces: dict[str, list[Path]],
    candidates: set[str],
) -> tuple[dict[str, set[str]], int]:
    references: dict[str, set[str]] = {}
    total_bytes = 0

    for surface, paths in surfaces.items():
        found: set[str] = set()
        for path in paths:
            text, size = _read_bounded_text(path)
            total_bytes += size
            if total_bytes > MAX_SOURCE_TOTAL_BYTES:
                raise CrossConfigReferenceAuditError("SOURCE_TOTAL_SIZE_LIMIT_EXCEEDED")
            for match in ENTITY_TOKEN_RE.finditer(text):
                value = match.group(1)
                if value in candidates:
                    found.add(value)
        references[surface] = found

    return references, total_bytes


def analyze_reference_coverage(
    *,
    candidates: set[str],
    surface_references: dict[str, set[str]],
) -> dict[str, Any]:
    referenced_surfaces: dict[str, set[str]] = defaultdict(set)
    for surface, values in surface_references.items():
        for entity_id in values:
            if entity_id not in candidates:
                raise CrossConfigReferenceAuditError("REFERENCE_CANDIDATE_SET_DRIFT")
            referenced_surfaces[entity_id].add(surface)

    referenced = set(referenced_surfaces)
    unreferenced = candidates - referenced
    multiple_surface = {
        entity_id
        for entity_id, surfaces in referenced_surfaces.items()
        if len(surfaces) > 1
    }

    if unreferenced:
        decision = READY_DECISION
        reasons = ["UNREFERENCED_IN_REVIEWED_CORPUS_PRESENT"]
    else:
        decision = NO_CANDIDATES_DECISION
        reasons = []

    return {
        "decision": decision,
        "reasons": reasons,
        "registry_candidate_count": len(candidates),
        "referenced_in_reviewed_corpus_count": len(referenced),
        "unreferenced_in_reviewed_corpus_count": len(unreferenced),
        "multiple_surface_reference_count": len(multiple_surface),
        "surface_reference_candidate_counts": {
            surface: len(values)
            for surface, values in sorted(surface_references.items())
        },
        "claims": claim_report(),
    }


def build_live_report(
    *,
    config_root: Path,
    dashboard_title: str,
    expected_version: str,
    running_version: str,
) -> dict[str, Any]:
    if running_version != expected_version:
        return blocked_report("HOME_ASSISTANT_VERSION_MISMATCH")

    try:
        phase4a = build_phase4a_report(
            config_root=config_root,
            dashboard_title=dashboard_title,
            expected_version=expected_version,
            running_version=running_version,
        )
        if phase4a.get("decision") == "BLOCKED":
            reasons = phase4a.get("reasons") or ["PHASE4A_BASELINE_BLOCKED"]
            return blocked_report(str(reasons[0]))

        root = config_root.resolve(strict=True)
        (
            _owner_path,
            owner_kind,
            _owner_payload,
            _dashboard_key,
            _definition,
            active_dashboard,
        ) = resolve_binding_owner(root, dashboard_title)
        active_root = active_dashboard.parent.resolve(strict=True)

        surfaces, source_file_count, source_bytes = _collect_source_files(root, active_root)
        candidates = _read_registry_candidates(root)
        references, scanned_bytes = _surface_candidate_references(surfaces, candidates)
        if scanned_bytes != source_bytes:
            raise CrossConfigReferenceAuditError("SOURCE_BYTE_ACCOUNTING_DRIFT")

        coverage = analyze_reference_coverage(
            candidates=candidates,
            surface_references=references,
        )

        phase4a_dashboard = phase4a["dashboard"]
        return {
            "schema": 1,
            "decision": coverage["decision"],
            "reasons": coverage["reasons"],
            "home_assistant": {
                "expected_version": expected_version,
                "running_version": running_version,
                "version_match": True,
            },
            "binding": {
                "resolved": True,
                "owner_kind": owner_kind,
            },
            "active_tree": phase4a["active_tree"],
            "dashboard": {
                "structure": phase4a_dashboard["structure"],
                "guard": phase4a_dashboard["guard"],
            },
            "corpus": {
                "source_file_count": source_file_count,
                "source_total_bytes": source_bytes,
                "surface_file_counts": {
                    surface: len(paths)
                    for surface, paths in sorted(surfaces.items())
                },
                "symlink_traversal": False,
                "secret_runtime_backup_roots_traversed": False,
                "registry_source_whitelist_count": 1,
            },
            "coverage": {
                key: value
                for key, value in coverage.items()
                if key not in {"decision", "reasons", "claims"}
            },
            "claims": coverage["claims"],
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }
    except (
        ActivationPlanError,
        CandidateError,
        ContentCleanupAuditError,
        CrossConfigReferenceAuditError,
    ) as exc:
        reason = getattr(exc, "reason", "CROSS_CONFIG_REFERENCE_AUDIT_FAILED")
        return blocked_report(str(reason))
    except Exception:
        return blocked_report("CROSS_CONFIG_REFERENCE_AUDIT_FAILED")


def running_home_assistant_version() -> str:
    try:
        from homeassistant.const import __version__
    except (ImportError, AttributeError) as exc:
        raise CrossConfigReferenceAuditError("HOME_ASSISTANT_VERSION_UNAVAILABLE") from exc
    return str(__version__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit entity-reference coverage across bounded Home Assistant config "
            "surfaces. The tool is strictly read-only."
        )
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--expected-version", default=EXPECTED_HA_VERSION)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.audit:
        report = blocked_report("AUDIT_GATE_REQUIRED")
    else:
        try:
            running = running_home_assistant_version()
        except CrossConfigReferenceAuditError as exc:
            report = blocked_report(exc.reason)
        else:
            report = build_live_report(
                config_root=args.config_root,
                dashboard_title=args.dashboard_title,
                expected_version=args.expected_version,
                running_version=running,
            )

    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))

    return 1 if report.get("decision") == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
