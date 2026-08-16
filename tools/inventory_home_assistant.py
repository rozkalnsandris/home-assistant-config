#!/usr/bin/env python3
"""Run the public-safe Home Assistant inventory payload read-only via Docker."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "tools" / "ha_inventory_payload.py"
DEFAULT_OUTPUT = ROOT / "exports" / "live-inventory.json"


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def select_container(rows: Iterable[tuple[str, str]], explicit: str | None = None) -> str:
    rows = list(rows)
    if explicit:
        if not any(name == explicit for name, _image in rows):
            raise RuntimeError(f"Requested container is not running: {explicit}")
        return explicit

    candidates = [
        (name, image)
        for name, image in rows
        if "home-assistant/home-assistant" in image.lower()
        or "homeassistant/home-assistant" in image.lower()
        or "homeassistant" in name.lower()
        or "home-assistant" in name.lower()
    ]
    if len(candidates) == 1:
        return candidates[0][0]
    if not candidates:
        raise RuntimeError(
            "No running Home Assistant Docker container was detected; use --container NAME"
        )
    names = ", ".join(name for name, _image in candidates)
    raise RuntimeError(f"Multiple Home Assistant candidates detected ({names}); use --container NAME")


def running_containers(docker: str) -> list[tuple[str, str]]:
    result = _run([docker, "ps", "--format", "{{.Names}}\t{{.Image}}"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "docker ps failed")

    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name, sep, image = line.partition("\t")
        if not sep:
            continue
        rows.append((name.strip(), image.strip()))
    return rows


def run_inventory(docker: str, container: str) -> dict:
    payload = PAYLOAD.read_text(encoding="utf-8")
    result = _run([docker, "exec", "-i", container, "python", "-"], input_text=payload)
    if result.returncode != 0:
        message = result.stderr.strip() or "Home Assistant inventory payload failed"
        raise RuntimeError(message)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Inventory payload did not return valid JSON") from exc

    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RuntimeError("Unexpected inventory schema")
    return data


def write_output(data: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read Home Assistant /config metadata without exporting secret values or runtime state."
        )
    )
    parser.add_argument("--container", help="Explicit running Home Assistant container name")
    parser.add_argument("--docker", default="docker", help="Docker CLI executable")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Local JSON output path (default: exports/live-inventory.json)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Also print the sanitized inventory JSON to stdout",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        container = select_container(running_containers(args.docker), args.container)
        data = run_inventory(args.docker, container)
        write_output(data, args.output)
    except (OSError, RuntimeError) as exc:
        print(f"Inventory failed: {exc}", file=sys.stderr)
        return 1

    print(f"Sanitized inventory written to: {args.output}", file=sys.stderr)
    if args.stdout:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
