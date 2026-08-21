# Heater RETIRE bounded privileged replacement

Issue #127 documents a production-gate failure in which the exact one-shot
authorization was consumed but the first bounded replacement did not change the
target. Read-only evidence proved the live targets and their parent directory
require privilege for same-parent atomic replacement and ownership preservation.

This document defines the source remediation only. It does not authorize a
production execution.

## Boundary

The Home Assistant production gate remains a normal operator process. It still
owns:

- exact Git SHA/tree/parent and Home Assistant version gates;
- fresh hardened RETIRE dry-run;
- runtime and Scheduler safety checks;
- private candidate materialization;
- persistent rollback retention;
- one-shot authorization consumption;
- Home Assistant check_config and restart orchestration;
- automatic rollback state machine.

The whole gate must not be run as root merely to cross the file-ownership
boundary.

Only the two already-bounded config-file apply/rollback replacements may use the
privileged bridge.

## Canonical launcher

`tools/run_heater_retire_production_gate.py` remains the canonical entry point.

The previously reviewed orchestrator is preserved in
`tools/run_heater_retire_production_gate_impl.py`. The canonical launcher
installs three narrow adapters before delegating to that unchanged
implementation:

1. privileged replacement preflight at the existing rollback-bundle boundary;
2. privileged-aware bounded apply;
3. privileged-aware bounded rollback.

User-owned/writable targets keep using the original direct verifier path. The
privileged worker is used only when target ownership or parent permissions
require it.

## Pre-consumption privilege gate

Before rollback retention returns to the orchestrator, the adapter requires a
read-only privilege preflight for every bounded target that needs elevation.

The privileged worker must prove, without changing the target:

- effective UID is root;
- target is the expected bounded ordinal and regular non-symlink;
- target metadata still matches the captured mode/UID/GID;
- apply preimage is still the exact captured original;
- target parent is a regular directory, on a writable filesystem, and writable
  by the privileged worker;
- worker output satisfies the privacy contract.

The worker is invoked through noninteractive `sudo -n`. Therefore a future
operator flow must establish sudo credentials before entering the one-shot
authorization boundary. Missing/expired credentials fail closed before a future
authorization marker is consumed.

## Private request transport

Private target paths and candidate/original bytes are never command-line
arguments.

The normal process serializes them only to the privileged worker's stdin. The
worker emits only bounded ordinal/phase/classification/fsync booleans and
sanitized reason codes. stderr is never copied into public gate output.

The worker runs Python isolated mode (`-I`) and uses only the standard library.

## Source trust

Every privileged-worker invocation first requires the exact checkout worktree to
be clean, including non-ignored untracked files. This supplements the existing
SHA/tree/parent gates because the privileged worker executes source from the
reviewed checkout.

A dirty worktree fails closed with a sanitized reason and performs no bounded
replacement.

## Privileged atomic replacement

For apply, the root worker revalidates exact target metadata and original bytes,
then:

1. creates a sibling temp file in the target parent;
2. sets the captured UID/GID and mode;
3. writes the exact candidate bytes;
4. flushes and fsyncs the temp file;
5. atomically replaces the bounded target;
6. fsyncs the parent directory;
7. rereads the target and verifies candidate bytes plus exact UID/GID/mode.

The unprivileged caller independently rereads and revalidates the same final
classification and metadata before reporting `equals_candidate`.

Rollback uses the same privileged path with the captured original bytes and
requires final `equals_original`, exact UID/GID/mode and parent fsync.

Any privileged apply failure is mapped back into the existing
`VerifiedReplaceError`/automatic rollback state machine. Any privileged rollback
failure remains a production incident.

## Unchanged safety properties

This remediation does not authorize or add:

- Scheduler service calls;
- Scheduler storage writes;
- helper or automation toggles;
- heater actuation;
- Home Assistant reload;
- retained rollback cleanup;
- a usable production authorization phrase.

The old source-bound authorization that exposed the ownership defect is consumed
forever and cannot be reused.

After this source change is reviewed, merged by explicit owner instruction and
validated on the new exact merged source, a new hardened RETIRE dry-run is still
required. Only a full PASS from that new exact source may permit formulation of
a completely new one-shot production authorization.
