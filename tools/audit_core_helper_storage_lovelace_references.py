#!/usr/bin/env python3
"""Audit Phase 4D core-helper candidates against bounded Lovelace storage dashboards."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from tools.audit_cross_config_entity_references import (
    DEFAULT_CONFIG_ROOT,
    DEFAULT_DASHBOARD_TITLE,
    ENTITY_TOKEN_RE,
    EXPECTED_HA_VERSION,
    running_home_assistant_version,
)
from tools.stratify_unreferenced_core_helpers import (
    CORE_HELPER,
    CORE_NON_HELPER,
    UNRESOLVED,
    EXPECTED_REFERENCED_COUNT,
    EXPECTED_REGISTRY_CANDIDATE_COUNT,
    EXPECTED_SOURCE_FILE_COUNT,
    EXPECTED_SOURCE_TOTAL_BYTES,
    EXPECTED_UNREFERENCED_COUNT,
    CoreHelperStratificationError,
    _classify_core_platform,
    _installed_core_components_root,
    _private_unreferenced_platforms,
    build_live_report as build_phase4d_report,
)

REFERENCES_PRESENT_DECISION = "CORE_HELPER_STORAGE_LOVELACE_REFERENCES_PRESENT"
NO_REFERENCES_DECISION = "NO_CORE_HELPER_STORAGE_LOVELACE_REFERENCES"

EXPECTED_CORE_HELPER_COUNT = 4
EXPECTED_CORE_NON_HELPER_COUNT = 295
EXPECTED_NON_CORE_OR_UNRESOLVED_COUNT = 31

MAX_STORAGE_INDEX_BYTES = 2 * 1024 * 1024
MAX_STORAGE_DASHBOARD_BYTES = 16 * 1024 * 1024
MAX_TOTAL_STORAGE_BYTES = 64 * 1024 * 1024
MAX_STORAGE_DASHBOARDS = 64
MAX_JSON_NODES = 500_000
MAX_JSON_DEPTH = 128

SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class StorageLovelaceAuditError(RuntimeError):
    """Sanitized Phase 4E failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LovelaceStorageSpec:
    """Installed-Core Lovelace storage key contract."""

    default_key: str
    named_key_template: str
    dashboards_key: str


def privacy_report() -> dict[str, bool]:
    return {
        "raw_yaml_emitted": False,
        "raw_storage_json_emitted": False,
        "private_paths_emitted": False,
        "storage_keys_emitted": False,
        "dashboard_ids_emitted": False,
        "dashboard_url_paths_emitted": False,
        "dashboard_titles_emitted": False,
        "card_payloads_emitted": False,
        "actions_services_or_urls_emitted": False,
        "entity_ids_emitted": False,
        "registry_platform_names_emitted": False,
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


def _validate_storage_key(key: object, reason: str) -> str:
    if not isinstance(key, str) or SAFE_KEY_RE.fullmatch(key) is None:
        raise StorageLovelaceAuditError(reason)
    if key in {".", ".."} or "/" in key or "\\" in key:
        raise StorageLovelaceAuditError(reason)
    return key


def _validate_storage_spec(spec: LovelaceStorageSpec) -> LovelaceStorageSpec:
    default_key = _validate_storage_key(spec.default_key, "LOVELACE_DEFAULT_STORAGE_KEY_DRIFT")
    dashboards_key = _validate_storage_key(spec.dashboards_key, "LOVELACE_DASHBOARDS_STORAGE_KEY_DRIFT")

    template = spec.named_key_template
    if not isinstance(template, str) or template.count("{}") != 1:
        raise StorageLovelaceAuditError("LOVELACE_NAMED_STORAGE_TEMPLATE_DRIFT")
    probe = template.format("phase4e_probe")
    _validate_storage_key(probe, "LOVELACE_NAMED_STORAGE_TEMPLATE_DRIFT")
    if "/" in template or "\\" in template or ".." in template:
        raise StorageLovelaceAuditError("LOVELACE_NAMED_STORAGE_TEMPLATE_DRIFT")

    if len({default_key, dashboards_key, probe}) != 3:
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_KEY_COLLISION")
    return spec


def _installed_lovelace_storage_spec() -> LovelaceStorageSpec:
    try:
        from homeassistant.components.lovelace import dashboard
    except ImportError as exc:
        raise StorageLovelaceAuditError("LOVELACE_CORE_MODULE_UNAVAILABLE") from exc

    spec = LovelaceStorageSpec(
        default_key=getattr(dashboard, "CONFIG_STORAGE_KEY_DEFAULT", None),
        named_key_template=getattr(dashboard, "CONFIG_STORAGE_KEY", None),
        dashboards_key=getattr(dashboard, "DASHBOARDS_STORAGE_KEY", None),
    )
    return _validate_storage_spec(spec)


def _storage_root(config_root: Path) -> Path:
    root = config_root.resolve(strict=True)
    storage = root / ".storage"
    if storage.is_symlink() or not storage.is_dir():
        raise StorageLovelaceAuditError("STORAGE_ROOT_INVALID")
    resolved = storage.resolve(strict=True)
    if resolved.parent != root:
        raise StorageLovelaceAuditError("STORAGE_ROOT_ESCAPE")
    return resolved


def _read_json_store(
    storage_root: Path,
    key: str,
    *,
    max_bytes: int,
    missing_ok: bool,
) -> tuple[dict[str, Any] | None, int]:
    safe_key = _validate_storage_key(key, "LOVELACE_STORAGE_KEY_DRIFT")
    path = storage_root / safe_key
    if not path.exists():
        if missing_ok:
            return None, 0
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_FILE_MISSING")
    if path.is_symlink() or not path.is_file():
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_FILE_NOT_REGULAR")
    resolved = path.resolve(strict=True)
    if resolved.parent != storage_root:
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_PATH_ESCAPE")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_STAT_FAILED") from exc
    if size > max_bytes:
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_SIZE_LIMIT_EXCEEDED")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_PARSE_FAILED") from exc
    if not isinstance(payload, dict):
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_WRAPPER_DRIFT")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise StorageLovelaceAuditError("LOVELACE_STORAGE_WRAPPER_DRIFT")
    return payload, size


def _dashboard_ids_from_index(payload: dict[str, Any] | None) -> list[str]:
    if payload is None:
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_INDEX_DRIFT")
    items = data.get("items")
    if not isinstance(items, list):
        raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_INDEX_DRIFT")
    if len(items) > MAX_STORAGE_DASHBOARDS:
        raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_COUNT_LIMIT_EXCEEDED")

    ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_INDEX_DRIFT")
        dashboard_id = item.get("id")
        if not isinstance(dashboard_id, str) or SAFE_ID_RE.fullmatch(dashboard_id) is None:
            raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_ID_DRIFT")
        if dashboard_id in seen:
            raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_DUPLICATE_ID")
        seen.add(dashboard_id)
        ids.append(dashboard_id)
    return ids


def _store_config(payload: dict[str, Any]) -> Any:
    data = payload.get("data")
    if not isinstance(data, dict) or "config" not in data:
        raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_STORE_DRIFT")
    config = data.get("config")
    if config is not None and not isinstance(config, (dict, list)):
        raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_STORE_DRIFT")
    return config


def _candidate_tokens_in_json(value: Any, candidates: set[str]) -> set[str]:
    found: set[str] = set()
    node_count = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_JSON_NODES:
            raise StorageLovelaceAuditError("LOVELACE_JSON_NODE_LIMIT_EXCEEDED")
        if depth > MAX_JSON_DEPTH:
            raise StorageLovelaceAuditError("LOVELACE_JSON_DEPTH_LIMIT_EXCEEDED")

        if isinstance(node, str):
            for match in ENTITY_TOKEN_RE.finditer(node):
                token = match.group(0)
                if token in candidates:
                    found.add(token)
            return
        if node is None or isinstance(node, (bool, int, float)):
            return
        if isinstance(node, list):
            for item in node:
                visit(item, depth + 1)
            return
        if isinstance(node, dict):
            for key, item in node.items():
                if not isinstance(key, str):
                    raise StorageLovelaceAuditError("LOVELACE_JSON_KEY_DRIFT")
                visit(key, depth + 1)
                visit(item, depth + 1)
            return
        raise StorageLovelaceAuditError("LOVELACE_JSON_TYPE_DRIFT")

    visit(value, 0)
    return found


def _private_core_helper_candidates(
    *,
    config_root: Path,
    dashboard_title: str,
    core_components_root: Path,
) -> set[str]:
    unreferenced, _source_files, _source_bytes = _private_unreferenced_platforms(
        config_root=config_root,
        dashboard_title=dashboard_title,
    )
    platform_buckets: dict[str, str] = {}
    for platform in sorted(set(unreferenced.values())):
        bucket, _manifest_read = _classify_core_platform(core_components_root, platform)
        platform_buckets[platform] = bucket
    return {
        entity_id
        for entity_id, platform in unreferenced.items()
        if platform_buckets[platform] == CORE_HELPER
    }


def build_live_report(
    *,
    config_root: Path,
    dashboard_title: str,
    expected_version: str,
    running_version: str,
    core_components_root: Path | None = None,
    storage_spec: LovelaceStorageSpec | None = None,
    expected_registry_candidate_count: int = EXPECTED_REGISTRY_CANDIDATE_COUNT,
    expected_referenced_count: int = EXPECTED_REFERENCED_COUNT,
    expected_unreferenced_count: int = EXPECTED_UNREFERENCED_COUNT,
    expected_core_helper_count: int = EXPECTED_CORE_HELPER_COUNT,
    expected_core_non_helper_count: int = EXPECTED_CORE_NON_HELPER_COUNT,
    expected_unresolved_count: int = EXPECTED_NON_CORE_OR_UNRESOLVED_COUNT,
    expected_source_file_count: int | None = EXPECTED_SOURCE_FILE_COUNT,
    expected_source_total_bytes: int | None = EXPECTED_SOURCE_TOTAL_BYTES,
) -> dict[str, Any]:
    if running_version != expected_version:
        return blocked_report("HOME_ASSISTANT_VERSION_MISMATCH")

    try:
        core_root = (
            core_components_root.resolve(strict=True)
            if core_components_root is not None
            else _installed_core_components_root()
        )
        phase4d = build_phase4d_report(
            config_root=config_root,
            dashboard_title=dashboard_title,
            expected_version=expected_version,
            running_version=running_version,
            core_components_root=core_root,
            expected_registry_candidate_count=expected_registry_candidate_count,
            expected_referenced_count=expected_referenced_count,
            expected_unreferenced_count=expected_unreferenced_count,
            expected_source_file_count=expected_source_file_count,
            expected_source_total_bytes=expected_source_total_bytes,
        )
        if phase4d.get("decision") == "BLOCKED":
            reasons = phase4d.get("reasons") or ["PHASE4D_BASELINE_BLOCKED"]
            return blocked_report(str(reasons[0]))

        provenance = phase4d.get("provenance")
        if not isinstance(provenance, dict):
            raise StorageLovelaceAuditError("PHASE4D_REPORT_FORMAT_DRIFT")
        if provenance.get("core_helper_unreferenced_candidate_count") != expected_core_helper_count:
            raise StorageLovelaceAuditError("PHASE4D_CORE_HELPER_COUNT_DRIFT")
        if provenance.get("core_non_helper_unreferenced_candidate_count") != expected_core_non_helper_count:
            raise StorageLovelaceAuditError("PHASE4D_CORE_NON_HELPER_COUNT_DRIFT")
        if provenance.get("non_core_or_unresolved_unreferenced_candidate_count") != expected_unresolved_count:
            raise StorageLovelaceAuditError("PHASE4D_UNRESOLVED_COUNT_DRIFT")

        helper_candidates = _private_core_helper_candidates(
            config_root=config_root,
            dashboard_title=dashboard_title,
            core_components_root=core_root,
        )
        if len(helper_candidates) != expected_core_helper_count:
            raise StorageLovelaceAuditError("PRIVATE_CORE_HELPER_COUNT_DRIFT")

        spec = _validate_storage_spec(storage_spec or _installed_lovelace_storage_spec())
        storage_root = _storage_root(config_root)

        index_payload, index_bytes = _read_json_store(
            storage_root,
            spec.dashboards_key,
            max_bytes=MAX_STORAGE_INDEX_BYTES,
            missing_ok=True,
        )
        dashboard_ids = _dashboard_ids_from_index(index_payload)

        store_keys: list[str] = [spec.default_key]
        store_keys.extend(spec.named_key_template.format(item_id) for item_id in dashboard_ids)
        if len(set(store_keys)) != len(store_keys):
            raise StorageLovelaceAuditError("LOVELACE_DASHBOARD_STORAGE_KEY_COLLISION")

        candidate_store_counts: Counter[str] = Counter()
        inspected_store_count = 0
        default_store_present = False
        named_store_count = 0
        total_storage_bytes = index_bytes

        for index, key in enumerate(store_keys):
            payload, size = _read_json_store(
                storage_root,
                key,
                max_bytes=MAX_STORAGE_DASHBOARD_BYTES,
                missing_ok=True,
            )
            if payload is None:
                continue
            total_storage_bytes += size
            if total_storage_bytes > MAX_TOTAL_STORAGE_BYTES:
                raise StorageLovelaceAuditError("LOVELACE_TOTAL_STORAGE_SIZE_LIMIT_EXCEEDED")
            inspected_store_count += 1
            if index == 0:
                default_store_present = True
            else:
                named_store_count += 1
            config = _store_config(payload)
            if config is None:
                continue
            for entity_id in _candidate_tokens_in_json(config, helper_candidates):
                candidate_store_counts[entity_id] += 1

        referenced_count = len(candidate_store_counts)
        unreferenced_after_count = expected_core_helper_count - referenced_count
        if unreferenced_after_count < 0:
            raise StorageLovelaceAuditError("STORAGE_REFERENCE_ACCOUNTING_DRIFT")
        multiple_store_count = sum(1 for count in candidate_store_counts.values() if count > 1)

        if referenced_count:
            decision = REFERENCES_PRESENT_DECISION
            reasons = ["CORE_HELPER_STORAGE_LOVELACE_REFERENCES_PRESENT"]
        else:
            decision = NO_REFERENCES_DECISION
            reasons = []

        active_tree = phase4d.get("active_tree")
        dashboard = phase4d.get("dashboard")
        if not isinstance(active_tree, dict) or not isinstance(dashboard, dict):
            raise StorageLovelaceAuditError("PHASE4D_REPORT_FORMAT_DRIFT")

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
            "phase4d_baseline": {
                "registry_candidate_count": expected_registry_candidate_count,
                "referenced_in_reviewed_corpus_count": expected_referenced_count,
                "unreferenced_in_reviewed_corpus_count": expected_unreferenced_count,
                "core_helper_unreferenced_candidate_count": expected_core_helper_count,
                "core_non_helper_unreferenced_candidate_count": expected_core_non_helper_count,
                "non_core_or_unresolved_unreferenced_candidate_count": expected_unresolved_count,
            },
            "storage_lovelace": {
                "dashboard_index_present": index_payload is not None,
                "allowed_dashboard_store_count": len(store_keys),
                "inspected_dashboard_store_count": inspected_store_count,
                "default_store_present": default_store_present,
                "named_store_count": named_store_count,
                "allowed_storage_total_bytes": total_storage_bytes,
                "core_helper_candidate_count": expected_core_helper_count,
                "referenced_candidate_count": referenced_count,
                "unreferenced_after_yaml_and_storage_lovelace_count": unreferenced_after_count,
                "referenced_by_multiple_storage_dashboards_count": multiple_store_count,
            },
            "storage_scope": {
                "installed_core_storage_constants_only": True,
                "recursive_storage_traversal": False,
                "unrelated_storage_files_opened": False,
                "auth_stores_opened": False,
                "core_config_entries_opened": False,
                "restore_state_or_history_opened": False,
                "custom_component_storage_opened": False,
                "entity_registry_read_only_for_candidate_reconstruction": True,
            },
            "claims": claim_report(),
            "privacy": privacy_report(),
            "mutation": mutation_report(),
        }
    except (
        CoreHelperStratificationError,
        StorageLovelaceAuditError,
        OSError,
    ) as exc:
        reason = getattr(exc, "reason", "STORAGE_LOVELACE_AUDIT_FAILED")
        return blocked_report(str(reason))
    except Exception:
        return blocked_report("STORAGE_LOVELACE_AUDIT_FAILED")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit Phase 4D core-helper candidates against only installed-Core "
            "Lovelace storage dashboard stores. Strictly read-only."
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
        report = blocked_report("STORAGE_LOVELACE_AUDIT_GATE_REQUIRED")
    else:
        try:
            running = running_home_assistant_version()
        except Exception:
            report = blocked_report("HOME_ASSISTANT_VERSION_UNAVAILABLE")
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
