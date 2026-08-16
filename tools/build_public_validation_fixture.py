#!/usr/bin/env python3
"""Build an isolated Home Assistant fixture from public source and dummy bindings."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE_NAME = "home-assistant-version.txt"
PUBLIC_SOURCE_FILES = (
    "configuration.yaml",
    "automations.yaml",
    "scripts.yaml",
    "scenes.yaml",
)
PUBLIC_PACKAGE_FILES = (
    "packages/heater_scheduler.yaml",
    "packages/izmaksas.yaml",
    "packages/silditajs.yaml",
)
VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]+\.[0-9]+$")

DUMMY_PRIVATE_FILES = {
    "private/customize.yaml": """\
sensor.ci_placeholder:
  friendly_name: CI Placeholder
""",
    "private/http.yaml": "{}\n",
    "private/lovelace.yaml": """\
mode: storage
""",
    "private/utility_meter.yaml": """\
ci_energy_daily:
  source: sensor.ci_energy
  cycle: daily
""",
    "private/recorder.yaml": """\
purge_keep_days: 7
commit_interval: 30
""",
}

DUMMY_SECRETS = """\
heater_switch_entity: switch.ci_heater
heater_schedule_initial_time: "12:34:00"
electricity_price_per_kwh: 0.1234
electricity_fixed_monthly: 1.23
"""

DUMMY_THEME = """\
ci_placeholder:
  primary-color: "#000000"
"""


def read_home_assistant_version(root: Path = ROOT) -> str:
    version = (root / VERSION_FILE_NAME).read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError(
            f"{VERSION_FILE_NAME} must contain one exact release version such as 2026.8.2"
        )
    return version


def _required_sources(source_root: Path) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for relative_name in (*PUBLIC_SOURCE_FILES, *PUBLIC_PACKAGE_FILES):
        source = source_root / relative_name
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError(
                f"Required public source file is missing or not a regular file: {relative_name}"
            )
        sources.append((relative_name, source))
    return sources


def _write_text(output: Path, relative_name: str, content: str) -> Path:
    destination = output / relative_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return destination


def build_fixture(source_root: Path, output: Path) -> list[Path]:
    source_root = source_root.resolve()
    output = output.resolve()
    sources = _required_sources(source_root)

    if output.exists():
        raise FileExistsError(f"Refusing to reuse existing output directory: {output}")
    output.mkdir(parents=True)

    written: list[Path] = []
    for relative_name, source in sources:
        destination = output / relative_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        written.append(destination)

    for relative_name, content in DUMMY_PRIVATE_FILES.items():
        written.append(_write_text(output, relative_name, content))

    written.append(_write_text(output, "secrets.yaml", DUMMY_SECRETS))
    written.append(_write_text(output, "themes/ci-placeholder.yaml", DUMMY_THEME))
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        help="New directory to create for the isolated validation fixture",
    )
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="Print the repository-pinned Home Assistant version and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.print_version:
        print(read_home_assistant_version())
        return 0
    if args.output is None:
        raise SystemExit("--output is required unless --print-version is used")
    read_home_assistant_version()
    build_fixture(ROOT, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
