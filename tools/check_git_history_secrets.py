#!/usr/bin/env python3
"""Fail closed if reachable Git history contains obvious secret material or forbidden runtime paths."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

FORBIDDEN_PATH_PARTS = {".storage", ".cloud"}
FORBIDDEN_ROOTS = {"backup", "backups", "media", "evidence", "exports"}
FORBIDDEN_NAMES = {"secrets.yaml", ".env"}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".agekey", ".db", ".sqlite", ".sqlite3", ".log", ".tar", ".tgz"}

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key material", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token-shaped value", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("JWT/token-shaped value", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("Bearer credential", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)),
    ("credential-bearing URL", re.compile(r"https?://[^\s/:@]+:[^\s/@]+@[^\s]+", re.IGNORECASE)),
)

MAX_BLOB_BYTES = 2_000_000


def git(*args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )


def path_problem(path_text: str) -> str | None:
    path = PurePosixPath(path_text)
    parts = set(path.parts)
    if path.name in FORBIDDEN_NAMES:
        return f"forbidden historical filename: {path.name}"
    if parts & FORBIDDEN_PATH_PARTS:
        return "forbidden Home Assistant runtime/private directory in history"
    if path.parts and path.parts[0] in FORBIDDEN_ROOTS:
        return "forbidden runtime/backup root in history"
    if path.name.startswith("home-assistant_v2.db"):
        return "Home Assistant recorder database present in history"
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return f"forbidden historical file suffix: {path.suffix}"
    return None


def main() -> int:
    failures: list[str] = []
    seen: set[str] = set()

    objects = git("rev-list", "--objects", "--all").stdout.splitlines()
    for line in objects:
        if not line.strip():
            continue
        sha, *rest = line.split(" ", 1)
        path_text = rest[0] if rest else ""
        if path_text:
            problem = path_problem(path_text)
            if problem:
                failures.append(f"{path_text}: {problem}")

        if sha in seen:
            continue
        seen.add(sha)

        obj_type = git("cat-file", "-t", sha).stdout.strip()
        if obj_type != "blob":
            continue
        size = int(git("cat-file", "-s", sha).stdout.strip())
        if size > MAX_BLOB_BYTES:
            continue

        data = git("cat-file", "-p", sha, text=False).stdout
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for label, pattern in PATTERNS:
            if pattern.search(text):
                failures.append(f"{path_text or sha}: {label} detected in reachable Git history")

    if failures:
        print("Git history secret scan FAILED:", file=sys.stderr)
        for item in sorted(set(failures)):
            print(f"- {item}", file=sys.stderr)
        return 1

    print("Git history secret scan PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
