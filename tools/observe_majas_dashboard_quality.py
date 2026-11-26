#!/usr/bin/env python3
"""Observe an evolving private Sections dashboard without enabling migration.

The historical exact-shape audit and activation planner are unchanged.
This observer reports anonymous measurements, never an apply-ready decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools import audit_majas_dashboard_quality as quality
from tools import audit_majas_dashboard_quality_legacy as legacy
from tools.materialize_majas_dashboard_candidate import (
    TaggedValue,
    load_mapping,
    structural_counts,
)


class ObservationError(RuntimeError):
    """Private observation failed; never serialize exception details."""


def _bounded(path: Path, root: Path) -> Path:
    # Check the lexical path before resolution so a symlink cannot be hidden.
    import os

    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise ObservationError()
    for part in path.relative_to(root).parts:
        if part in {".storage", ".cloud", "deps", "custom_components"} or part.startswith("secrets"):
            raise ObservationError()
    for entry in (path, *path.parents):
        if entry == root:
            break
        if entry.is_symlink():
            raise ObservationError()
    if path.resolve(strict=True) != path:
        raise ObservationError()
    return path


def _mapping(path: Path, root: Path) -> dict[str, Any]:
    path = _bounded(path, root)
    if path.suffix != ".yaml" or not path.is_file():
        raise ObservationError()
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ObservationError()
    return load_mapping(path)


def _list(value: Any, owner: Path, root: Path) -> list[Any]:
    if isinstance(value, TaggedValue):
        if value.tag != "!include_dir_list" or not isinstance(value.value, str):
            raise ObservationError()
        if Path(value.value).is_absolute():
            raise ObservationError()
        directory = _bounded(owner.parent / value.value, root)
        if not directory.is_dir() or directory == root:
            raise ObservationError()
        # This observer supports the flat include directories of the modular
        # dashboard, not arbitrary recursive includes or runtime directories.
        entries = sorted(directory.iterdir())
        if not entries or len(entries) > 100:
            raise ObservationError()
        if any(
            entry.is_symlink() or not entry.is_file() or entry.suffix != ".yaml"
            for entry in entries
        ):
            raise ObservationError()
        return [
            (_bounded(entry, root), _mapping(entry, root))
            for entry in entries
        ]
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ObservationError()
    return [(owner, item) for item in value]


def load_registered_dashboard(
    config_root: Path, dashboard_title: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read only the registry owner and its bounded modular dashboard."""
    if config_root.is_symlink():
        raise ObservationError()
    root = config_root.resolve(strict=True)
    owner = root / "configuration.yaml"
    config = _mapping(owner, root)
    lovelace = config.get("lovelace")
    if isinstance(lovelace, TaggedValue):
        if lovelace.tag != "!include" or not isinstance(lovelace.value, str):
            raise ObservationError()
        if Path(lovelace.value).is_absolute():
            raise ObservationError()
        lovelace = _mapping(root / lovelace.value, root)
    if not isinstance(lovelace, dict):
        raise ObservationError()
    registry = lovelace.get("dashboards")
    if not isinstance(registry, dict):
        raise ObservationError()
    matches = [
        definition for definition in registry.values()
        if isinstance(definition, dict) and definition.get("title") == dashboard_title
    ]
    if len(matches) != 1 or matches[0].get("mode") != "yaml":
        raise ObservationError()
    filename = matches[0].get("filename")
    if not isinstance(filename, str) or Path(filename).is_absolute():
        raise ObservationError()
    dashboard = _bounded(root / filename, root)
    dashboard_root = dashboard.parent
    if dashboard_root == root:
        raise ObservationError()
    payload = _mapping(dashboard, dashboard_root)
    views = []
    for view_file, raw_view in _list(payload.get("views"), dashboard, dashboard_root):
        if not isinstance(raw_view, dict) or raw_view.get("type") != "sections":
            raise ObservationError()
        view = dict(raw_view)
        sections = []
        for _, section in _list(view.get("sections"), view_file, dashboard_root):
            if not isinstance(section, dict):
                raise ObservationError()
            sections.append(section)
        view["sections"] = sections
        views.append(view)
    payload["views"] = views
    return payload, lovelace


def _validate_value(value: Any, depth: int = 0) -> None:
    """Reject unresolved tags, recursive aliases, malformed keys and deep trees."""
    if depth > 64 or isinstance(value, TaggedValue):
        raise ObservationError()
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ObservationError()
            _validate_value(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_value(item, depth + 1)


def observe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _validate_value(payload)
    cards = quality.iter_top_level_cards(payload)
    if not cards or any(view.get("type") != "sections" for view in payload["views"]):
        raise ObservationError()
    if any("cards" in view for view in payload["views"]):
        raise ObservationError()

    def validate_card(card: Any, depth: int = 0) -> None:
        if depth > 32 or not isinstance(card, dict):
            raise ObservationError()
        if not isinstance(card.get("type"), str) or not card["type"]:
            raise ObservationError()
        if legacy._grid_options_class(card)[0] == "invalid":
            raise ObservationError()
        if quality._section_mode_class(card) == "invalid":
            raise ObservationError()
        if "cards" in card:
            if not isinstance(card["cards"], list) or not card["cards"]:
                raise ObservationError()
            for child in card["cards"]:
                validate_card(child, depth + 1)
        if "card" in card:
            validate_card(card["card"], depth + 1)

    for card in cards:
        validate_card(card)
    layout = legacy._layout_metrics(payload, cards)
    modernization = quality._modernization_metrics(payload, cards)
    return {
        "schema": 1,
        "decision": "OBSERVATION_ONLY",
        "migration_ready": False,
        "structure": structural_counts(payload),
        "layout": layout,
        "actions": legacy._action_metrics(cards),
        "modernization": modernization,
        "coverage": {
            "rendered_behavior_verified": False,
            "template_inheritance_fully_resolved": False,
            "action_safety_proven": False,
        },
        "privacy": quality.privacy_report(),
        "mutation": quality.mutation_report(),
    }


def build_report(
    *, config_root: Path, dashboard_title: str, running_version: str
) -> dict[str, Any]:
    if running_version != quality.EXPECTED_HA_VERSION:
        return quality.blocked_report("HOME_ASSISTANT_VERSION_MISMATCH")
    try:
        payload, lovelace = load_registered_dashboard(config_root, dashboard_title)
        report = observe_payload(payload)
        report["lovelace"] = quality.classify_lovelace_mode(lovelace)
        report["dependencies"] = quality.detect_button_card_capability(config_root)
        return report
    except Exception:
        return quality.blocked_report("CURRENT_DASHBOARD_OBSERVATION_FAILED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=quality.DEFAULT_CONFIG_ROOT)
    parser.add_argument("--dashboard-title", default=quality.DEFAULT_DASHBOARD_TITLE)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    report = quality.blocked_report("AUDIT_GATE_REQUIRED")
    if args.audit:
        try:
            report = build_report(
                config_root=args.config_root,
                dashboard_title=args.dashboard_title,
                running_version=quality.running_home_assistant_version(),
            )
        except Exception:
            report = quality.blocked_report("CURRENT_DASHBOARD_OBSERVATION_FAILED")
    if args.stdout:
        print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["decision"] == "OBSERVATION_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
