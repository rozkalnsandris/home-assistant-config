#!/usr/bin/env python3
"""Canonical heater RETIRE production gate with bounded privilege bridging."""

from __future__ import annotations

import sys

from tools import privileged_atomic_replace as _privileged
from tools import run_heater_retire_production_gate_impl as _impl

_original_create_rollback_bundle = _impl.preflight.create_rollback_bundle


def _create_rollback_bundle_with_privileged_preflight(*args, **kwargs):
    states = kwargs.get("states")
    if not isinstance(states, list):
        raise _impl.common.GateError("BOUNDED_STATE_COUNT_INVALID")
    try:
        _privileged.preflight_privileged_replacements(states)
    except _privileged.PrivilegedReplacePreflightError as exc:
        raise _impl.common.GateError(exc.reason) from exc
    return _original_create_rollback_bundle(*args, **kwargs)


_impl.apply_verified_replace = _privileged.apply_verified_replace_with_privilege
_impl.rollback_verified_replace = (
    _privileged.rollback_verified_replace_with_privilege
)
_impl.preflight.create_rollback_bundle = (
    _create_rollback_bundle_with_privileged_preflight
)


if __name__ == "__main__":
    raise SystemExit(_impl.main())

sys.modules[__name__] = _impl
