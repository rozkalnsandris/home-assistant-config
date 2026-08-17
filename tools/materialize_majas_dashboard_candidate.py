#!/usr/bin/env python3
"""Create and validate a private side-by-side Mājas dashboard candidate safely."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Any, Callable

import yaml

EXPECTED_HA_VERSION = "2026.8.2"
EXPECTED_VIEW_COUNT = 1
EXPECTED_SECTION_COUNT = 3
EXPECTED_CARD_COUNT = 12
EXPECTED_CUSTOM_CARD_COUNT = 11
EXPECTED_DISTINCT_CUSTOM_CARD_TYPE_COUNT = 1
DEFAULT_CONFIG_ROOT = Path("/config")
DEFAULT_DASHBOARD_TITLE = "Mājas YAML"
SECTION_FILENAMES = ("00_section.yaml", "10_section.yaml", "20_section.yaml")


class CandidateError(RuntimeError):
    """Sanitized candidate materialization failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(eq=True)
class TaggedValue:
    """Preserve an unknown Home Assistant YAML tag without resolving it."""

    tag: str
    value: Any


class DashboardLoader(yaml.SafeLoader):
    """Private-safe loader that preserves unknown Home Assistant tags."""


class DashboardDumper(yaml.SafeDumper):
    """Dumper that restores preserved Home Assistant tags."""


def _unknown_tag(
    loader: DashboardLoader, tag_suffix: str, node: yaml.Node
) -> TaggedValue:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node, deep=True)
    else:
        value = None
    return TaggedValue(f"!{tag_suffix}", value)


def _tagged_value(dumper: DashboardDumper, value: TaggedValue) -> yaml.Node:
    node = dumper.represent_data(value.value)
    node.tag = value.tag
    return node


DashboardLoader.add_multi_constructor("!", _unknown_tag)
DashboardDumper.add_representer(TaggedValue, _tagged_value)


def load_yaml(path: Path) -> Any:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=DashboardLoader)


def load_mapping(path: Path) -> dict[str, Any]:
    payload = load_yaml(path)
    if not isinstance(payload, dict):
        raise CandidateError("YAML_MAPPING_REQUIRED")
    return payload


def dump_yaml(payload: Any) -> str:
    return yaml.dump(
        payload,
        Dumper=DashboardDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def _card_metrics(card: Any) -> dict[str, Any]:
    metrics = {"cards": 0, "custom_cards": 0, "custom_types": set()}
    if not isinstance(card, dict):
        return metrics
    metrics["cards"] = 1
    card_type = card.get("type")
    if isinstance(card_type, str) and card_type.startswith("custom:"):
        metrics["custom_cards"] = 1
        metrics["custom_types"].add(card_type)
    children: list[Any] = []
    if isinstance(card.get("cards"), list):
        children.extend(card["cards"])
    if isinstance(card.get("card"), dict):
        children.append(card["card"])
    for child in children:
        child_metrics = _card_metrics(child)
        metrics["cards"] += child_metrics["cards"]
        metrics["custom_cards"] += child_metrics["custom_cards"]
        metrics["custom_types"].update(child_metrics["custom_types"])
    return metrics


def structural_counts(payload: dict[str, Any]) -> dict[str, int]:
    views = payload.get("views")
    if not isinstance(views, list):
        return {
            "view_count": 0,
            "section_count": 0,
            "card_count": 0,
            "custom_card_count": 0,
            "distinct_custom_card_type_count": 0,
        }
    section_count = 0
    card_count = 0
    custom_card_count = 0
    custom_types: set[str] = set()
    for view in views:
        if not isinstance(view, dict):
            continue
        cards = view.get("cards")
        if isinstance(cards, list):
            for card in cards:
                metrics = _card_metrics(card)
                card_count += metrics["cards"]
                custom_card_count += metrics["custom_cards"]
                custom_types.update(metrics["custom_types"])
        sections = view.get("sections")
        if isinstance(sections, list):
            section_count += len(sections)
            for section in sections:
                if not isinstance(section, dict):
                    continue
                section_cards = section.get("cards")
                if not isinstance(section_cards, list):
                    continue
                for card in section_cards:
                    metrics = _card_metrics(card)
                    card_count += metrics["cards"]
                    custom_card_count += metrics["custom_cards"]
                    custom_types.update(metrics["custom_types"])
    return {
        "view_count": len(views),
        "section_count": section_count,
        "card_count": card_count,
        "custom_card_count": custom_card_count,
        "distinct_custom_card_type_count": len(custom_types),
    }


def expected_shape(counts: dict[str, int]) -> bool:
    return counts == {
        "view_count": EXPECTED_VIEW_COUNT,
        "section_count": EXPECTED_SECTION_COUNT,
        "card_count": EXPECTED_CARD_COUNT,
        "custom_card_count": EXPECTED_CUSTOM_CARD_COUNT,
        "distinct_custom_card_type_count": EXPECTED_DISTINCT_CUSTOM_CARD_TYPE_COUNT,
    }


def split_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not expected_shape(structural_counts(payload)):
        raise CandidateError("BASELINE_STRUCTURE_MISMATCH")
    views = payload.get("views")
    if not isinstance(views, list) or len(views) != 1:
        raise CandidateError("BASELINE_STRUCTURE_MISMATCH")
    view = views[0]
    if not isinstance(view, dict):
        raise CandidateError("VIEW_MAPPING_REQUIRED")
    sections = view.get("sections")
    if not isinstance(sections, list) or len(sections) != 3:
        raise CandidateError("BASELINE_STRUCTURE_MISMATCH")
    if any(not isinstance(section, dict) for section in sections):
        raise CandidateError("SECTION_MAPPING_REQUIRED")
    dashboard_root = copy.deepcopy(payload)
    dashboard_root.pop("views")
    view_root = copy.deepcopy(view)
    view_root.pop("sections")
    return {
        "dashboard_root": dashboard_root,
        "view_root": view_root,
        "sections": copy.deepcopy(sections),
    }


def _bounded_existing_file(config_root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = config_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if resolved != config_root and config_root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


def resolve_active_dashboard(config_root: Path, title: str) -> Path:
    try:
        root = config_root.resolve(strict=True)
    except OSError as exc:
        raise CandidateError("CONFIG_ROOT_UNAVAILABLE") from exc
    if not root.is_dir():
        raise CandidateError("CONFIG_ROOT_UNAVAILABLE")
    config = load_mapping(root / "configuration.yaml")
    lovelace = config.get("lovelace")
    if isinstance(lovelace, TaggedValue) and lovelace.tag == "!include":
        include_path = _bounded_existing_file(root, lovelace.value)
        if include_path is None:
            raise CandidateError("LOVELACE_INCLUDE_UNRESOLVED")
        lovelace = load_mapping(include_path)
    if not isinstance(lovelace, dict):
        raise CandidateError("LOVELACE_MAPPING_UNAVAILABLE")
    dashboards = lovelace.get("dashboards")
    if not isinstance(dashboards, dict):
        raise CandidateError("DASHBOARD_REGISTRY_UNAVAILABLE")
    matches: list[Path] = []
    for definition in dashboards.values():
        if not isinstance(definition, dict) or definition.get("title") != title:
            continue
        dashboard_path = _bounded_existing_file(root, definition.get("filename"))
        if dashboard_path is not None:
            matches.append(dashboard_path)
    if len(matches) != 1:
        raise CandidateError("DASHBOARD_BINDING_NOT_UNIQUE")
    return matches[0]


def validate_destination(config_root: Path, destination: Path) -> Path:
    try:
        root = config_root.resolve(strict=True)
    except OSError as exc:
        raise CandidateError("CONFIG_ROOT_UNAVAILABLE") from exc
    candidate = destination
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.exists() or candidate.is_symlink():
        raise CandidateError("DESTINATION_ALREADY_EXISTS")
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise CandidateError("DESTINATION_PARENT_UNAVAILABLE") from exc
    if parent != root and root not in parent.parents:
        raise CandidateError("DESTINATION_OUTSIDE_CONFIG")
    bounded = parent / candidate.name
    if bounded == root or root not in bounded.parents:
        raise CandidateError("DESTINATION_OUTSIDE_CONFIG")
    return bounded


def _write_new(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(dump_yaml(payload))


def _resolve_candidate_value(value: Any, source_file: Path) -> Any:
    if isinstance(value, TaggedValue) and value.tag == "!include_dir_list":
        if not isinstance(value.value, str):
            raise CandidateError("CANDIDATE_INCLUDE_INVALID")
        directory = (source_file.parent / value.value).resolve(strict=True)
        if not directory.is_dir():
            raise CandidateError("CANDIDATE_INCLUDE_INVALID")
        files = sorted(
            (item for item in directory.rglob("*.yaml") if item.is_file()),
            key=lambda item: item.relative_to(directory).as_posix(),
        )
        return [_resolve_candidate_value(load_yaml(item), item) for item in files]
    if isinstance(value, dict):
        return {
            key: _resolve_candidate_value(item, source_file)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_candidate_value(item, source_file) for item in value]
    return value


def load_candidate_tree(candidate_root: Path) -> dict[str, Any]:
    dashboard = candidate_root / "dashboard.yaml"
    payload = _resolve_candidate_value(load_mapping(dashboard), dashboard)
    if not isinstance(payload, dict):
        raise CandidateError("CANDIDATE_ROOT_INVALID")
    return payload


def validate_with_home_assistant(
    dashboard_path: Path,
    config_root: Path,
    expected_version: str = EXPECTED_HA_VERSION,
) -> dict[str, bool]:
    try:
        from homeassistant.const import __version__
        from homeassistant.util.yaml import Secrets, load_yaml as ha_load_yaml
    except ImportError as exc:
        raise CandidateError("HOME_ASSISTANT_LOADER_UNAVAILABLE") from exc
    if __version__ != expected_version:
        raise CandidateError("HOME_ASSISTANT_VERSION_MISMATCH")
    try:
        parsed = ha_load_yaml(dashboard_path, Secrets(config_root))
    except Exception as exc:
        raise CandidateError("HOME_ASSISTANT_CANDIDATE_PARSE_FAILED") from exc
    if not isinstance(parsed, dict):
        raise CandidateError("HOME_ASSISTANT_CANDIDATE_PARSE_FAILED")
    return {"version_match": True, "candidate_parses": True}


Validator = Callable[[Path, Path, str], dict[str, bool]]
WriteHook = Callable[[int, Path], None]


def blocked_report(
    reason: str, *, cleaned: bool = False, before: dict[str, int] | None = None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": [reason],
        "validation": {
            "candidate_round_trip_equivalent": False,
            "home_assistant_version_match": False,
            "home_assistant_candidate_parses": False,
        },
        "privacy": {
            "raw_private_values_emitted": False,
            "private_paths_emitted": False,
        },
        "mutation": {
            "candidate_tree_created": False,
            "candidate_tree_cleaned_after_failure": cleaned,
            "active_dashboard_modified": False,
            "live_dashboard_binding_changed": False,
            "storage_write": False,
            "reload_or_restart": False,
        },
    }
    if before is not None:
        report["structure"] = {"before": before}
    return report


def materialize_candidate(
    *,
    config_root: Path,
    dashboard_title: str,
    destination: Path,
    expected_version: str = EXPECTED_HA_VERSION,
    validator: Validator = validate_with_home_assistant,
    write_hook: WriteHook | None = None,
) -> dict[str, Any]:
    candidate_root: Path | None = None
    created = False
    before: dict[str, int] | None = None
    try:
        source_path = resolve_active_dashboard(config_root, dashboard_title)
        payload = load_mapping(source_path)
        before = structural_counts(payload)
        model = split_payload(payload)
        resolved_root = config_root.resolve(strict=True)
        candidate_root = validate_destination(resolved_root, destination)

        dashboard_root = copy.deepcopy(model["dashboard_root"])
        dashboard_root["views"] = TaggedValue("!include_dir_list", "views")
        view_root = copy.deepcopy(model["view_root"])
        view_root["sections"] = TaggedValue(
            "!include_dir_list", "../sections/view_00"
        )

        candidate_root.mkdir(mode=0o700)
        created = True
        (candidate_root / "views").mkdir()
        section_root = candidate_root / "sections" / "view_00"
        section_root.mkdir(parents=True)

        writes = [
            (candidate_root / "dashboard.yaml", dashboard_root),
            (candidate_root / "views" / "00_view.yaml", view_root),
            *[
                (section_root / name, section)
                for name, section in zip(
                    SECTION_FILENAMES,
                    model["sections"],
                    strict=True,
                )
            ],
        ]
        for index, (path, content) in enumerate(writes, start=1):
            _write_new(path, content)
            if write_hook is not None:
                write_hook(index, path)

        assembled = load_candidate_tree(candidate_root)
        after = structural_counts(assembled)
        if assembled != payload or before != after:
            raise CandidateError("CANDIDATE_EQUIVALENCE_FAILED")

        ha_result = validator(
            candidate_root / "dashboard.yaml",
            resolved_root,
            expected_version,
        )
        if not (
            ha_result.get("version_match") is True
            and ha_result.get("candidate_parses") is True
        ):
            raise CandidateError("HOME_ASSISTANT_VALIDATION_FAILED")

        return {
            "schema": 1,
            "decision": "READY_FOR_PRIVATE_CANDIDATE_REVIEW",
            "reasons": [],
            "structure": {
                "before": before,
                "after": after,
                "ordered_section_count": len(model["sections"]),
                "candidate_file_count": len(writes),
            },
            "validation": {
                "candidate_round_trip_equivalent": True,
                "home_assistant_version_match": True,
                "home_assistant_candidate_parses": True,
            },
            "privacy": {
                "raw_private_values_emitted": False,
                "private_paths_emitted": False,
            },
            "mutation": {
                "candidate_tree_created": True,
                "candidate_tree_cleaned_after_failure": False,
                "active_dashboard_modified": False,
                "live_dashboard_binding_changed": False,
                "storage_write": False,
                "reload_or_restart": False,
            },
        }
    except CandidateError as exc:
        cleaned = False
        if created and candidate_root is not None:
            shutil.rmtree(candidate_root, ignore_errors=True)
            cleaned = not candidate_root.exists()
        return blocked_report(exc.reason, cleaned=cleaned, before=before)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError):
        cleaned = False
        if created and candidate_root is not None:
            shutil.rmtree(candidate_root, ignore_errors=True)
            cleaned = not candidate_root.exists()
        return blocked_report(
            "CANDIDATE_MATERIALIZATION_FAILED",
            cleaned=cleaned,
            before=before,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a bounded side-by-side Mājas dashboard split candidate. "
            "The tool is inert unless --materialize is explicitly supplied."
        )
    )
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-version", default=EXPECTED_HA_VERSION)
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.materialize:
        report = blocked_report("OWNER_GATE_REQUIRED")
    else:
        report = materialize_candidate(
            config_root=args.config_root,
            dashboard_title=args.dashboard_title,
            destination=args.destination,
            expected_version=args.expected_version,
        )
    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    return (
        0
        if report.get("decision") == "READY_FOR_PRIVATE_CANDIDATE_REVIEW"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
