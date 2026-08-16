#!/usr/bin/env python3
"""Build an isolated Home Assistant validation fixture from public-safe tracked source."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE_NAME = "home-assistant-version.txt"
PUBLIC_SOURCE_FILES = ("automations.yaml", "scripts.yaml", "scenes.yaml")
VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]+\.[0-9]+$")

MINIMAL_CONFIGURATION = """\
automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml
"""


def read_home_assistant_version(root: Path = ROOT) -> str:
    version = (root / VERSION_FILE_NAME).read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError(
            f"{VERSION_FILE_NAME} must contain one exact release version such as 2026.8.2"
        )
    return version


def build_fixture(source_root: Path, output: Path) -> list[Path]:
    source_root = source_root.resolve()
    output = output.resolve()

    sources: list[tuple[str, Path]] = []
    for relative_name in PUBLIC_SOURCE_FILES:
        source = source_root / relative_name
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError(
                f"Required public source file is missing or not a regular file: {relative_name}"
            )
        sources.append((relative_name, source))

    if output.exists():
        raise FileExistsError(f"Refusing to reuse existing output directory: {output}")
    output.mkdir(parents=True)

    written: list[Path] = []
    for relative_name, source in sources:
        destination = output / relative_name
        shutil.copyfile(source, destination)
        written.append(destination)

    configuration = output / "configuration.yaml"
    configuration.write_text(MINIMAL_CONFIGURATION, encoding="utf-8")
    written.append(configuration)
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
