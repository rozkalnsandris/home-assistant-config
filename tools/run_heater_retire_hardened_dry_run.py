#!/usr/bin/env python3
"""Canonical hardened RETIRE dry-run launcher with the proven HA version probe."""

from __future__ import annotations

import sys

from tools import run_heater_retire_hardened_dry_run_impl as _impl


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


_impl._running_version = _running_version


if __name__ == "__main__":
    raise SystemExit(_impl.main())

# Preserve the canonical import surface so existing tests and callers continue
# to patch and exercise the implementation module directly.
sys.modules[__name__] = _impl
