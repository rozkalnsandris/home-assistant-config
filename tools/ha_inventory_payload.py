#!/usr/bin/env python3
"""Read-only Home Assistant /config inventory payload.

This module is designed to run inside the Home Assistant container. It emits only
bounded metadata needed to decide what may later be reviewed for Git. It never
opens known secret/runtime files or traverses known private runtime directories.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
from pathlib import Path
from typing import Any

CONFIG_ROOT = Path(os.environ.get("HA_CONFIG_DIR", "/config"))

TRACK_FILES = {
    "configuration.yaml",
    "automations.yaml",
    "scripts.yaml",
    "scenes.yaml",
    "ui-lovelace.yaml",
}
TRACK_DIRS = {"packages", "templates", "themes", "blueprints", "www", "dashboards"}
BACKUP_ONLY_ROOTS = {"backup", "backups", "media"}
IGNORE_ROOTS = {".storage", ".cloud", "deps", "tts", "__pycache__"}
REVIEW_ROOTS = {"custom_components"}
SENSITIVE_EXACT = {"secrets.yaml", ".uuid", ".HA_VERSION"}
SENSITIVE_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".tar",
    ".tgz",
    ".zip",
}

LOVELACE_START_RE = re.compile(r"^(?P<indent>\s*)lovelace\s*:\s*(?:#.*)?$")
MODE_YAML_RE = re.compile(r"^\s*mode\s*:\s*['\"]?yaml['\"]?\s*(?:#.*)?$", re.IGNORECASE)
FILENAME_RE = re.compile(r"^\s*filename\s*:\s*(?P<value>[^#]+?)\s*(?:#.*)?$")
SAFE_YAML_PATH_RE = re.compile(r"^[\w./-]+\.ya?ml$", re.IGNORECASE | re.UNICODE)


def _suffix(name: str) -> str:
    lowered = name.lower()
    for suffix in sorted(SENSITIVE_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            return suffix
    return ""


def classify_entry(name: str, *, is_dir: bool) -> tuple[str, bool]:
    """Return (classification, sensitive) without inspecting entry contents."""

    lowered = name.lower()

    if name in SENSITIVE_EXACT or lowered == "secrets.yaml":
        return "IGNORE", True
    if lowered in IGNORE_ROOTS:
        return "IGNORE", True
    if lowered in BACKUP_ONLY_ROOTS:
        return "BACKUP_ONLY", True
    if lowered.startswith("home-assistant_v2.db") or lowered.startswith("home-assistant.log"):
        return "BACKUP_ONLY", True
    if _suffix(lowered):
        return "BACKUP_ONLY", True
    if is_dir and lowered in REVIEW_ROOTS:
        return "REVIEW", False
    if is_dir and lowered in TRACK_DIRS:
        return "TRACK_CANDIDATE", False
    if not is_dir and lowered in TRACK_FILES:
        return "TRACK_CANDIDATE", False
    if not is_dir and lowered.endswith((".yaml", ".yml")):
        return "REVIEW", False
    return "REVIEW", False


def parse_lovelace_refs(configuration_text: str) -> dict[str, Any]:
    """Extract only bounded Lovelace metadata from configuration.yaml text."""

    lines = configuration_text.splitlines()
    block_present = False
    yaml_mode_seen = False
    filenames: list[str] = []
    in_block = False
    base_indent = 0

    for line in lines:
        if not in_block:
            match = LOVELACE_START_RE.match(line)
            if not match:
                continue
            block_present = True
            in_block = True
            base_indent = len(match.group("indent"))
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            in_block = False
            match = LOVELACE_START_RE.match(line)
            if match:
                block_present = True
                in_block = True
                base_indent = len(match.group("indent"))
            continue

        if MODE_YAML_RE.match(line):
            yaml_mode_seen = True

        filename_match = FILENAME_RE.match(line)
        if not filename_match:
            continue
        value = filename_match.group("value").strip().strip("'\"")
        if SAFE_YAML_PATH_RE.fullmatch(value):
            filenames.append(value)

    return {
        "configuration_block_present": block_present,
        "yaml_mode_seen": yaml_mode_seen,
        "dashboard_filenames": sorted(set(filenames)),
    }


def _home_assistant_version() -> str | None:
    try:
        return importlib.metadata.version("homeassistant")
    except importlib.metadata.PackageNotFoundError:
        return None


def inventory(config_root: Path = CONFIG_ROOT) -> dict[str, Any]:
    if not config_root.is_dir():
        raise RuntimeError("Home Assistant config directory is not available")

    entries: list[dict[str, Any]] = []
    root_dashboard_candidates: list[str] = []

    for entry in sorted(config_root.iterdir(), key=lambda item: item.name.lower()):
        is_dir = entry.is_dir() and not entry.is_symlink()
        classification, sensitive = classify_entry(entry.name, is_dir=is_dir)
        entries.append(
            {
                "name": entry.name,
                "kind": "directory" if is_dir else "file",
                "classification": classification,
                "sensitive_or_runtime": sensitive,
            }
        )

        lowered = entry.name.lower()
        if (
            not is_dir
            and lowered.endswith((".yaml", ".yml"))
            and ("lovelace" in lowered or "dashboard" in lowered)
        ):
            root_dashboard_candidates.append(entry.name)

    lovelace = {
        "configuration_block_present": False,
        "yaml_mode_seen": False,
        "dashboard_filenames": [],
    }
    configuration_path = config_root / "configuration.yaml"
    if configuration_path.is_file():
        # configuration.yaml is the only file whose contents are opened here, and
        # only bounded Lovelace keys are emitted. No unrelated values leave this process.
        configuration_text = configuration_path.read_text(encoding="utf-8", errors="replace")
        lovelace = parse_lovelace_refs(configuration_text)

    dashboard_candidates = sorted(
        set(root_dashboard_candidates) | set(lovelace["dashboard_filenames"])
    )

    return {
        "schema_version": 1,
        "home_assistant_version": _home_assistant_version(),
        "entries": entries,
        "lovelace": lovelace,
        "dashboard_candidates": dashboard_candidates,
        "safety": {
            "secret_values_emitted": False,
            "runtime_directory_children_traversed": False,
            "host_paths_emitted": False,
            "intended_for_local_review_only": True,
        },
    }


def main() -> int:
    print(json.dumps(inventory(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
