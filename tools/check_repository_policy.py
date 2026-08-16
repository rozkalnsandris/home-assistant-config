#!/usr/bin/env python3
"""Fail closed on tracked Home Assistant secrets/runtime artifacts."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PARTS = {".storage", ".cloud"}
FORBIDDEN_ROOTS = {"backup", "backups", "evidence", "exports", "media"}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".agekey",
    ".tar",
    ".tgz",
}

PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")
YAML_SECRET_RE = re.compile(
    r"^\s*(password|passwd|token|api_key|apikey|client_secret|access_token|refresh_token|secret)\s*:\s*(.+?)\s*(?:#.*)?$",
    re.IGNORECASE,
)
SAFE_VALUE_MARKERS = (
    "!secret",
    "${{ secrets.",
    "${{ github.",
    "placeholder",
    "example",
    "replace_me",
    "change_me",
)
SAFE_EXACT_VALUES = {"", "null", "~", "''", '""'}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(p.decode("utf-8")) for p in result.stdout.split(b"\0") if p]


def path_violations(path: Path) -> list[str]:
    problems: list[str] = []
    parts = set(path.parts)
    if path.name == "secrets.yaml":
        problems.append("real secrets.yaml must never be tracked")
    if parts & FORBIDDEN_PARTS:
        problems.append("Home Assistant runtime/private directory is forbidden")
    if path.parts and path.parts[0] in FORBIDDEN_ROOTS:
        problems.append("runtime/backup/evidence root is forbidden")
    if path.name.startswith("home-assistant_v2.db"):
        problems.append("Home Assistant recorder database is forbidden")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        problems.append(f"forbidden tracked file suffix: {path.suffix}")
    return problems


def content_violations(path: Path) -> list[str]:
    full = ROOT / path
    try:
        data = full.read_bytes()
    except OSError as exc:
        return [f"cannot read tracked file: {exc}"]

    if b"\0" in data or len(data) > 2_000_000:
        return []

    text = data.decode("utf-8", errors="replace")
    problems: list[str] = []

    if PRIVATE_KEY_RE.search(text):
        problems.append("private key material detected")
    if GITHUB_TOKEN_RE.search(text):
        problems.append("GitHub token-shaped value detected")

    if path.suffix.lower() in {".yaml", ".yml"} and path.name != "secrets.yaml.example":
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = YAML_SECRET_RE.match(line)
            if not match:
                continue
            value = match.group(2).strip()
            lower_value = value.lower()
            if value in SAFE_EXACT_VALUES or any(marker in lower_value for marker in SAFE_VALUE_MARKERS):
                continue
            problems.append(
                f"line {lineno}: secret-like YAML key must use !secret or a non-secret CI reference"
            )

    return problems


def main() -> int:
    failures: list[str] = []
    for path in tracked_files():
        for problem in path_violations(path):
            failures.append(f"{path}: {problem}")
        for problem in content_violations(path):
            failures.append(f"{path}: {problem}")

    if failures:
        print("Repository policy check FAILED:", file=sys.stderr)
        for item in failures:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("Repository policy check PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
