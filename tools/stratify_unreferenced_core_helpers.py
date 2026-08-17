#!/usr/bin/env python3
"""Stratify Phase 4C unreferenced entities by Home Assistant Core integration type."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from tools.audit_cross_config_entity_references import (
    DEFAULT_CONFIG_ROOT,
    DEFAULT_DASHBOARD_TITLE,
    ENTITY_TOKEN_RE,
    EXPECTED_HA_VERSION,
    MAX_REGISTRY_BYTES,
    MAX_REGISTRY_ENTITIES,
    REGISTRY_RELATIVE_PATH,
    CrossConfigReferenceAuditError,
    _collect_source_files,
    _require_regular_file,
    _surface_candidate_references,
    build_live_report as build_phase4c_report,
    running_home_assistant_version,
)
from tools.plan_majas_dashboard_activation import (
    ActivationPlanError,
    resolve_binding_owner,
)

READY_DECISION = "READY_FOR_PRIVATE_CORE_HELPER_EVIDENCE_REVIEW"
NO_CANDIDATES_DECISION = "NO_CORE_HELPER_UNREFERENCED_CANDIDATES"

EXPECTED_REGISTRY_CANDIDATE_COUNT = 371
EXPECTED_REFERENCED_COUNT = 41
EXPECTED_UNREFERENCED_COUNT = 330
EXPECTED_SOURCE_FILE_COUNT = 16
EXPECTED_SOURCE_TOTAL_BYTES = 116275

MAX_CORE_MANIFEST_BYTES = 512 * 1024
PLATFORM_RE = re.compile(r"^[a-z0-9_]+$")

CORE_HELPER = "CORE_HELPER_INTEGRATION"
CORE_NON_HELPER = "CORE_NON_HELPER_INTEGRATION"
UNRESOLVED = "NON_CORE_OR_UNRESOLVED"
BUCKETS = (CORE_HELPER, CORE_NON_HELPER, UNRESOLVED)


class CoreHelperStratificationError(RuntimeError):
    """Sanitized Phase 4D failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "private_paths_emitted": False,
        "entity_ids_emitted": False,
        "registry_payload_emitted": False,
        "registry_platform_names_emitted": False,
        "manifest_paths_emitted": False,
        "manifest_domain_names_emitted": False,
        "raw_manifest_payloads_emitted": False,
        "integration_names_emitted": False,
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
        "storage_write": False,
        "registry_modified": False,
        "helper_or_entity_removed": False,
        "core_manifest_modified": False,
        "custom_component_modified": False,
        "binding_changed": False,
        "grid_options_modified": False,
        "reload_or_restart": False,
    }


def claim_report() -> dict[str, bool]:
    return {
        "unused_claimed": False,
        "safe_to_remove_claimed": False,
        "automatic_removal_authorized": False,
        "helper_removal_candidate_claimed": False,
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


def _read_registry_entity_platforms(config_root: Path) -> dict[str, str]:
    registry = config_root / REGISTRY_RELATIVE_PATH
    _require_regular_file(registry, "ENTITY_REGISTRY_UNAVAILABLE")

    try:
        size = registry.stat().st_size
    except OSError as exc:
        raise CoreHelperStratificationError("ENTITY_REGISTRY_STAT_FAILED") from exc
    if size > MAX_REGISTRY_BYTES:
        raise CoreHelperStratificationError("ENTITY_REGISTRY_SIZE_LIMIT_EXCEEDED")

    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoreHelperStratificationError("ENTITY_REGISTRY_PARSE_FAILED") from exc

    if not isinstance(payload, dict):
        raise CoreHelperStratificationError("ENTITY_REGISTRY_FORMAT_DRIFT")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CoreHelperStratificationError("ENTITY_REGISTRY_FORMAT_DRIFT")
    entities = data.get("entities")
    if not isinstance(entities, list):
        raise CoreHelperStratificationError("ENTITY_REGISTRY_FORMAT_DRIFT")
    if len(entities) > MAX_REGISTRY_ENTITIES:
        raise CoreHelperStratificationError("ENTITY_REGISTRY_COUNT_LIMIT_EXCEEDED")

    result: dict[str, str] = {}
    for entry in entities:
        if not isinstance(entry, dict):
            raise CoreHelperStratificationError("ENTITY_REGISTRY_FORMAT_DRIFT")
        entity_id = entry.get("entity_id")
        platform = entry.get("platform")
        if not isinstance(entity_id, str) or ENTITY_TOKEN_RE.fullmatch(entity_id) is None:
            raise CoreHelperStratificationError("ENTITY_REGISTRY_FORMAT_DRIFT")
        if not isinstance(platform, str) or PLATFORM_RE.fullmatch(platform) is None:
            raise CoreHelperStratificationError("ENTITY_REGISTRY_PLATFORM_FORMAT_DRIFT")
        if entity_id in result:
            raise CoreHelperStratificationError("ENTITY_REGISTRY_DUPLICATE_ENTITY_ID")
        result[entity_id] = platform

    return result


def _installed_core_components_root() -> Path:
    try:
        import homeassistant
    except ImportError as exc:
        raise CoreHelperStratificationError("HOME_ASSISTANT_PACKAGE_UNAVAILABLE") from exc

    package_file = getattr(homeassistant, "__file__", None)
    if not package_file:
        raise CoreHelperStratificationError("HOME_ASSISTANT_PACKAGE_PATH_UNAVAILABLE")
    root = Path(package_file).resolve().parent / "components"
    if root.is_symlink() or not root.is_dir():
        raise CoreHelperStratificationError("CORE_COMPONENTS_ROOT_INVALID")
    return root.resolve(strict=True)


def _classify_core_platform(core_components_root: Path, platform: str) -> tuple[str, bool]:
    """Return bucket and whether a core manifest was read."""

    if PLATFORM_RE.fullmatch(platform) is None:
        raise CoreHelperStratificationError("ENTITY_REGISTRY_PLATFORM_FORMAT_DRIFT")

    component_dir = core_components_root / platform
    if not component_dir.exists():
        return UNRESOLVED, False
    if component_dir.is_symlink() or not component_dir.is_dir():
        raise CoreHelperStratificationError("CORE_COMPONENT_DIRECTORY_INVALID")

    manifest = component_dir / "manifest.json"
    if not manifest.exists():
        return UNRESOLVED, False
    if manifest.is_symlink() or not manifest.is_file():
        raise CoreHelperStratificationError("CORE_MANIFEST_NOT_REGULAR")

    try:
        size = manifest.stat().st_size
    except OSError as exc:
        raise CoreHelperStratificationError("CORE_MANIFEST_STAT_FAILED") from exc
    if size > MAX_CORE_MANIFEST_BYTES:
        raise CoreHelperStratificationError("CORE_MANIFEST_SIZE_LIMIT_EXCEEDED")

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoreHelperStratificationError("CORE_MANIFEST_PARSE_FAILED") from exc

    if not isinstance(payload, dict):
        raise CoreHelperStratificationError("CORE_MANIFEST_FORMAT_DRIFT")
    if payload.get("domain") != platform:
        raise CoreHelperStratificationError("CORE_MANIFEST_DOMAIN_MISMATCH")

    integration_type = payload.get("integration_type")
    if integration_type is None:
        return UNRESOLVED, True
    if not isinstance(integration_type, str) or not integration_type:
        raise CoreHelperStratificationError("CORE_MANIFEST_INTEGRATION_TYPE_DRIFT")
    if integration_type == "helper":
        return CORE_HELPER, True
    return CORE_NON_HELPER, True


def _private_unreferenced_platforms(
    *,
    config_root: Path,
    dashboard_title: str,
) -> tuple[dict[str, str], int, int]:
    root = config_root.resolve(strict=True)
    (
        _owner_path,
        _owner_kind,
        _owner_payload,
        _dashboard_key,
        _definition,
        active_dashboard,
    ) = resolve_binding_owner(root, dashboard_title)
    active_root = active_dashboard.parent.resolve(strict=True)

    surfaces, source_file_count, source_bytes = _collect_source_files(root, active_root)
    entity_platforms = _read_registry_entity_platforms(root)
    candidates = set(entity_platforms)
    references, scanned_bytes = _surface_candidate_references(surfaces, candidates)
    if scanned_bytes != source_bytes:
        raise CoreHelperStratificationError("SOURCE_BYTE_ACCOUNTING_DRIFT")

    referenced: set[str] = set()
    for values in references.values():
        referenced.update(values)
    unreferenced = {
        entity_id: entity_platforms[entity_id]
        for entity_id in candidates - referenced
    }
    return unreferenced, source_file_count, source_bytes


def build_live_report(
    *,
    config_root: Path,
    dashboard_title: str,
    expected_version: str,
    running_version: str,
    core_components_root: Path | None = None,
    expected_registry_candidate_count: int = EXPECTED_REGISTRY_CANDIDATE_COUNT,
    expected_referenced_count: int = EXPECTED_REFERENCED_COUNT,
    expected_unreferenced_count: int = EXPECTED_UNREFERENCED_COUNT,
    expected_source_file_count: int | None = EXPECTED_SOURCE_FILE_COUNT,
    expected_source_total_bytes: int | None = EXPECTED_SOURCE_TOTAL_BYTES,
) -> dict[str, Any]:
    if running_version != expected_version:
        return blocked_report("HOME_ASSISTANT_VERSION_MISMATCH")

    try:
        phase4c = build_phase4c_report(
            config_root=config_root,
            dashboard_title=dashboard_title,
            expected_version=expected_version,
            running_version=running_version,
        )
        if phase4c.get("decision") == "BLOCKED":
            reasons = phase4c.get("reasons") or ["PHASE4C_BASELINE_BLOCKED"]
            return blocked_report(str(reasons[0]))

        coverage = phase4c.get("coverage")
        if not isinstance(coverage, dict):
            raise CoreHelperStratificationError("PHASE4C_REPORT_FORMAT_DRIFT")
        if coverage.get("registry_candidate_count") != expected_registry_candidate_count:
            raise CoreHelperStratificationError("PHASE4C_REGISTRY_COUNT_DRIFT")
        if coverage.get("referenced_in_reviewed_corpus_count") != expected_referenced_count:
            raise CoreHelperStratificationError("PHASE4C_REFERENCED_COUNT_DRIFT")
        if coverage.get("unreferenced_in_reviewed_corpus_count") != expected_unreferenced_count:
            raise CoreHelperStratificationError("PHASE4C_UNREFERENCED_COUNT_DRIFT")

        unreferenced, source_file_count, source_bytes = _private_unreferenced_platforms(
            config_root=config_root,
            dashboard_title=dashboard_title,
        )
        if len(unreferenced) != expected_unreferenced_count:
            raise CoreHelperStratificationError("PRIVATE_UNREFERENCED_COUNT_DRIFT")
        if expected_source_file_count is not None and source_file_count != expected_source_file_count:
            raise CoreHelperStratificationError("PHASE4C_SOURCE_FILE_COUNT_DRIFT")
        if expected_source_total_bytes is not None and source_bytes != expected_source_total_bytes:
            raise CoreHelperStratificationError("PHASE4C_SOURCE_BYTE_COUNT_DRIFT")

        root = (
            core_components_root.resolve(strict=True)
            if core_components_root is not None
            else _installed_core_components_root()
        )
        if root.is_symlink() or not root.is_dir():
            raise CoreHelperStratificationError("CORE_COMPONENTS_ROOT_INVALID")

        platform_buckets: dict[str, str] = {}
        manifest_reads = 0
        for platform in sorted(set(unreferenced.values())):
            bucket, manifest_read = _classify_core_platform(root, platform)
            platform_buckets[platform] = bucket
            manifest_reads += int(manifest_read)

        candidate_bucket_counts = Counter(
            platform_buckets[platform] for platform in unreferenced.values()
        )
        bucket_counts = {
            bucket: int(candidate_bucket_counts.get(bucket, 0))
            for bucket in BUCKETS
        }
        if sum(bucket_counts.values()) != expected_unreferenced_count:
            raise CoreHelperStratificationError("PROVENANCE_BUCKET_ACCOUNTING_DRIFT")

        helper_count = bucket_counts[CORE_HELPER]
        if helper_count:
            decision = READY_DECISION
            reasons = ["CORE_HELPER_UNREFERENCED_CANDIDATES_PRESENT"]
        else:
            decision = NO_CANDIDATES_DECISION
            reasons = []

        active_tree = phase4c.get("active_tree")
        dashboard = phase4c.get("dashboard")
        if not isinstance(active_tree, dict) or not isinstance(dashboard, dict):
            raise CoreHelperStratificationError("PHASE4C_REPORT_FORMAT_DRIFT")

        return {
            "schema": 1,
            "decision": decision,
            "reasons": reasons,
            "home_assistant": {
                "expected_version": expected_version,
                "running_version": running_version,
                "version_match": True,
            },
            "active_tree": active_tree,
            "dashboard": dashboard,
            "phase4c_baseline": {
                "registry_candidate_count": expected_registry_candidate_count,
                "referenced_in_reviewed_corpus_count": expected_referenced_count,
                "unreferenced_in_reviewed_corpus_count": expected_unreferenced_count,
                "source_file_count": source_file_count,
                "source_total_bytes": source_bytes,
            },
            "provenance": {
                "core_helper_unreferenced_candidate_count": bucket_counts[CORE_HELPER],
                "core_non_helper_unreferenced_candidate_count": bucket_counts[CORE_NON_HELPER],
                "non_core_or_unresolved_unreferenced_candidate_count": bucket_counts[UNRESOLVED],
                "private_unique_platform_count": len(platform_buckets),
                "core_manifest_read_count": manifest_reads,
            },
            "manifest_guard": {
                "installed_core_manifest_source_only": True,
                "hardcoded_helper_allowlist_used": False,
                "custom_components_traversed": False,
                "manifest_domain_match_required": True,
                "manifest_reads_are_read_only": True,
            },
            "claims": claim_report(),
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }
    except (
        ActivationPlanError,
        CrossConfigReferenceAuditError,
        CoreHelperStratificationError,
        OSError,
    ) as exc:
        reason = getattr(exc, "reason", "CORE_HELPER_STRATIFICATION_FAILED")
        return blocked_report(str(reason))
    except Exception:
        return blocked_report("CORE_HELPER_STRATIFICATION_FAILED")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stratify Phase 4C unreferenced registry candidates by installed Home "
            "Assistant Core integration type. Strictly read-only."
        )
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--expected-version", default=EXPECTED_HA_VERSION)
    parser.add_argument("--stratify", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.stratify:
        report = blocked_report("STRATIFY_GATE_REQUIRED")
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
