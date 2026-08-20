#!/usr/bin/env python3
"""Canonical hardened RETIRE dry-run launcher with the proven HA version probe."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import reconcile_heater_retire_postincident as _postincident
from tools import run_heater_retire_hardened_dry_run_impl as _impl

TOOLS_PACKAGE_MARKER = ROOT / "tools" / "__init__.py"


def _running_version(docker: str, container: str) -> str:
    """Return the running Home Assistant version using the proven CLI contract."""
    result = _impl._run(
        [docker, "exec", container, "python", "-m", "homeassistant", "--version"]
    )
    if result.returncode != 0:
        raise RuntimeError("HA_VERSION_PROBE_FAILED")
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("HA_VERSION_PROBE_FAILED")
    return value


def _package_marker_destination(destination: str) -> str | None:
    marker = "/repo/"
    if marker not in destination:
        return None
    prefix, _remainder = destination.split(marker, 1)
    return f"{prefix}/repo/tools/__init__.py"


def _copy_into_container(
    docker: str,
    container: str,
    source: Path,
    destination: str,
) -> None:
    """Stage an explicit local tools package before each private worker file."""
    marker_destination = _package_marker_destination(destination)
    if marker_destination is not None:
        _impl._private_stage_original_copy_into_container(
            docker,
            container,
            TOOLS_PACKAGE_MARKER,
            marker_destination,
        )
    _impl._private_stage_original_copy_into_container(
        docker,
        container,
        source,
        destination,
    )


_impl._running_version = _running_version
_impl.collect_reconciliation = _postincident.collect_reconciliation
_impl.validate_reconciliation_report = _postincident.validate_reconciliation_report
if not hasattr(_impl, "_private_stage_original_copy_into_container"):
    _impl._private_stage_original_copy_into_container = _impl._copy_into_container
_impl._copy_into_container = _copy_into_container


if __name__ == "__main__":
    raise SystemExit(_impl.main())

# Preserve the canonical import surface so existing tests and callers continue
# to patch and exercise the implementation module directly.
sys.modules[__name__] = _impl
