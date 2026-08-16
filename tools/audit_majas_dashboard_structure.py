#!/usr/bin/env python3
"""Audit the active Mājas YAML dashboard without emitting private bindings."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_CONTAINER = "homeassistant"
DEFAULT_TITLE = "Mājas YAML"

RUNTIME_PROBE = r'''
import json
import sys
from pathlib import Path

import yaml

CONFIG_ROOT = Path("/config")
TARGET_TITLE = sys.argv[1]


class DashboardLoader(yaml.SafeLoader):
    pass


def unknown(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = None
    return {"__ha_tag__": "!" + tag_suffix, "value": value}


DashboardLoader.add_multi_constructor("!", unknown)


def load_mapping(path):
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
        payload = yaml.load(text, Loader=DashboardLoader)
    except Exception:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    return payload, text


def bounded_path(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = CONFIG_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
        root = CONFIG_ROOT.resolve(strict=True)
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    if not resolved.is_file():
        return None
    return resolved


def card_metrics(card):
    metrics = {
        "cards": 0,
        "horizontal_stack_cards": 0,
        "vertical_stack_cards": 0,
        "grid_cards": 0,
        "conditional_cards": 0,
        "custom_cards": 0,
        "custom_types": set(),
    }
    if not isinstance(card, dict):
        return metrics

    metrics["cards"] = 1
    card_type = card.get("type")
    if isinstance(card_type, str):
        if card_type == "horizontal-stack":
            metrics["horizontal_stack_cards"] = 1
        elif card_type == "vertical-stack":
            metrics["vertical_stack_cards"] = 1
        elif card_type == "grid":
            metrics["grid_cards"] = 1
        elif card_type == "conditional":
            metrics["conditional_cards"] = 1
        if card_type.startswith("custom:"):
            metrics["custom_cards"] = 1
            metrics["custom_types"].add(card_type)

    nested = []
    if isinstance(card.get("cards"), list):
        nested.extend(card["cards"])
    if isinstance(card.get("card"), dict):
        nested.append(card["card"])

    for child in nested:
        child_metrics = card_metrics(child)
        for key in (
            "cards",
            "horizontal_stack_cards",
            "vertical_stack_cards",
            "grid_cards",
            "conditional_cards",
            "custom_cards",
        ):
            metrics[key] += child_metrics[key]
        metrics["custom_types"].update(child_metrics["custom_types"])

    return metrics


def merge_metrics(target, source):
    for key in (
        "cards",
        "horizontal_stack_cards",
        "vertical_stack_cards",
        "grid_cards",
        "conditional_cards",
        "custom_cards",
    ):
        target[key] += source[key]
    target["custom_types"].update(source["custom_types"])


def complexity(count):
    if count <= 10:
        return "small"
    if count <= 25:
        return "medium"
    if count <= 50:
        return "large"
    if count <= 100:
        return "very-large"
    return "extreme"


def summarize_dashboard(text):
    try:
        payload = yaml.load(text, Loader=DashboardLoader)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    views = payload.get("views")
    if not isinstance(views, list) or not views:
        return None

    totals = {
        "cards": 0,
        "sections": 0,
        "horizontal_stack_cards": 0,
        "vertical_stack_cards": 0,
        "grid_cards": 0,
        "conditional_cards": 0,
        "custom_cards": 0,
        "custom_types": set(),
    }
    anonymous_views = []
    largest_section_cards = 0

    for index, view in enumerate(views):
        if not isinstance(view, dict):
            return None

        current = {
            "cards": 0,
            "sections": 0,
            "horizontal_stack_cards": 0,
            "vertical_stack_cards": 0,
            "grid_cards": 0,
            "conditional_cards": 0,
            "custom_cards": 0,
            "custom_types": set(),
        }

        cards = view.get("cards")
        if isinstance(cards, list):
            for card in cards:
                merge_metrics(current, card_metrics(card))

        sections = view.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                current["sections"] += 1
                section_card_count = 0
                section_cards = section.get("cards")
                if isinstance(section_cards, list):
                    for card in section_cards:
                        metrics = card_metrics(card)
                        section_card_count += metrics["cards"]
                        merge_metrics(current, metrics)
                largest_section_cards = max(
                    largest_section_cards,
                    section_card_count,
                )

        merge_metrics(totals, current)
        totals["sections"] += current["sections"]
        anonymous_views.append(
            {
                "view": "view_%02d" % index,
                "cards": current["cards"],
                "sections": current["sections"],
                "stack_cards": (
                    current["horizontal_stack_cards"]
                    + current["vertical_stack_cards"]
                ),
                "custom_cards": current["custom_cards"],
                "complexity": complexity(current["cards"]),
            }
        )

    largest_view_cards = max(item["cards"] for item in anonymous_views)
    return {
        "line_count": len(text.splitlines()),
        "view_count": len(views),
        "section_count": totals["sections"],
        "card_count": totals["cards"],
        "horizontal_stack_card_count": totals["horizontal_stack_cards"],
        "vertical_stack_card_count": totals["vertical_stack_cards"],
        "grid_card_count": totals["grid_cards"],
        "conditional_card_count": totals["conditional_cards"],
        "custom_card_count": totals["custom_cards"],
        "distinct_custom_card_type_count": len(totals["custom_types"]),
        "include_directive_count": text.count("!include"),
        "largest_view_card_count": largest_view_cards,
        "largest_view_complexity": complexity(largest_view_cards),
        "largest_section_card_count": largest_section_cards,
        "largest_section_complexity": complexity(largest_section_cards),
        "views": anonymous_views,
    }


config, _ = load_mapping(CONFIG_ROOT / "configuration.yaml")
if config is None:
    print(json.dumps({"resolved": False, "reason": "CONFIGURATION_UNREADABLE"}))
    raise SystemExit(0)

lovelace = config.get("lovelace")
if isinstance(lovelace, dict) and lovelace.get("__ha_tag__") == "!include":
    lovelace_path = bounded_path(lovelace.get("value"))
    if lovelace_path is None:
        print(json.dumps({"resolved": False, "reason": "LOVELACE_INCLUDE_UNRESOLVED"}))
        raise SystemExit(0)
    lovelace, _ = load_mapping(lovelace_path)

if not isinstance(lovelace, dict):
    print(json.dumps({"resolved": False, "reason": "LOVELACE_MAPPING_UNAVAILABLE"}))
    raise SystemExit(0)

dashboards = lovelace.get("dashboards")
if not isinstance(dashboards, dict):
    print(json.dumps({"resolved": False, "reason": "DASHBOARD_REGISTRY_UNAVAILABLE"}))
    raise SystemExit(0)

matches = []
for definition in dashboards.values():
    if not isinstance(definition, dict):
        continue
    if definition.get("title") != TARGET_TITLE:
        continue
    dashboard_path = bounded_path(definition.get("filename"))
    if dashboard_path is not None:
        matches.append(dashboard_path)

if len(matches) != 1:
    print(json.dumps({"resolved": False, "reason": "DASHBOARD_BINDING_NOT_UNIQUE"}))
    raise SystemExit(0)

try:
    dashboard_text = matches[0].read_text(encoding="utf-8", errors="strict")
except (OSError, UnicodeError):
    print(json.dumps({"resolved": False, "reason": "DASHBOARD_UNREADABLE"}))
    raise SystemExit(0)

structure = summarize_dashboard(dashboard_text)
if structure is None:
    print(json.dumps({"resolved": True, "reason": "DASHBOARD_STRUCTURE_UNPARSABLE"}))
    raise SystemExit(0)

print(
    json.dumps(
        {
            "resolved": True,
            "reason": "",
            "structure": structure,
            "privacy": {
                "dashboard_path_emitted": False,
                "raw_yaml_emitted": False,
                "entity_ids_emitted": False,
                "view_names_or_paths_emitted": False,
                "card_titles_emitted": False,
                "custom_card_type_names_emitted": False,
                "secrets_resolved": False,
            },
        },
        sort_keys=True,
    )
)
'''


def _run(
    command: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _require_success(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed")
    return result.stdout


def expected_version(path: Path = EXPECTED_VERSION_FILE) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("expected Home Assistant version is empty")
    return value


def source_sha() -> str:
    result = _run(["git", "rev-parse", "HEAD"])
    value = _require_success(result, "source SHA lookup").strip()
    if len(value) != 40:
        raise RuntimeError("source SHA lookup returned unexpected value")
    return value


def running_version(docker: str, container: str) -> str:
    result = _run(
        [docker, "exec", container, "python", "-m", "homeassistant", "--version"]
    )
    value = _require_success(result, "Home Assistant version lookup").strip()
    if not value:
        raise RuntimeError("Home Assistant version lookup returned no value")
    return value


def collect_runtime_probe(docker: str, container: str, title: str) -> dict[str, Any]:
    result = _run(
        [docker, "exec", "-i", container, "python", "-", title],
        input_text=RUNTIME_PROBE,
    )
    payload = _require_success(result, "live dashboard structure probe").strip()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("live dashboard structure probe returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("live dashboard structure probe returned unexpected data")
    return decoded


def build_report(
    *, sha: str, expected: str, running: str, probe: dict[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    hard_block = False

    if expected != running:
        reasons.append("HOME_ASSISTANT_VERSION_MISMATCH")
        hard_block = True

    resolved = probe.get("resolved") is True
    structure = probe.get("structure") if isinstance(probe.get("structure"), dict) else {}
    reason = probe.get("reason") if isinstance(probe.get("reason"), str) else ""

    if not resolved:
        reasons.append(reason or "DASHBOARD_NOT_RESOLVED")
        hard_block = True
    elif not structure:
        reasons.append(reason or "DASHBOARD_STRUCTURE_UNPARSABLE")

    privacy = probe.get("privacy") if isinstance(probe.get("privacy"), dict) else {}
    expected_false = (
        "dashboard_path_emitted",
        "raw_yaml_emitted",
        "entity_ids_emitted",
        "view_names_or_paths_emitted",
        "card_titles_emitted",
        "custom_card_type_names_emitted",
        "secrets_resolved",
    )
    if any(privacy.get(key) is not False for key in expected_false):
        reasons.append("PRIVACY_GUARD_FAILED")
        hard_block = True

    if not reasons:
        decision = "READY_FOR_SPLIT_DESIGN"
    elif hard_block:
        decision = "BLOCKED"
    else:
        decision = "NEEDS_REVIEW"

    return {
        "schema": 1,
        "decision": decision,
        "reasons": reasons,
        "source": {"sha": sha},
        "home_assistant": {
            "expected_version": expected,
            "running_version": running,
            "version_match": expected == running,
        },
        "dashboard": {
            "resolved": resolved,
            "structure": structure,
        },
        "privacy": privacy,
        "mutation": {
            "home_assistant_write": False,
            "dashboard_write": False,
            "storage_write": False,
            "reload_or_restart": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a private-safe structural audit of the active Mājas YAML dashboard."
    )
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--dashboard-title", default=DEFAULT_TITLE)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def blocked_report() -> dict[str, Any]:
    return {
        "schema": 1,
        "decision": "BLOCKED",
        "reasons": ["AUDIT_EXECUTION_FAILED"],
        "privacy": {
            "dashboard_path_emitted": False,
            "raw_yaml_emitted": False,
            "entity_ids_emitted": False,
            "view_names_or_paths_emitted": False,
            "card_titles_emitted": False,
            "custom_card_type_names_emitted": False,
            "secrets_resolved": False,
        },
        "mutation": {
            "home_assistant_write": False,
            "dashboard_write": False,
            "storage_write": False,
            "reload_or_restart": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(
            sha=source_sha(),
            expected=expected_version(),
            running=running_version(args.docker, args.container),
            probe=collect_runtime_probe(args.docker, args.container, args.dashboard_title),
        )
    except (OSError, RuntimeError):
        report = blocked_report()

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.stdout or args.output is None:
        sys.stdout.write(rendered)

    decision = report.get("decision")
    if decision == "READY_FOR_SPLIT_DESIGN":
        return 0
    if decision == "NEEDS_REVIEW":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
