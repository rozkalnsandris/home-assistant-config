# Heater RETIRE one-shot production gate

Issue #123 tracks the final production gate for the owner-selected heater RETIRE
transition. This document describes the reviewed source contract only. It does
not authorize a production execution.

## Source layout

The gate is split into small reviewable components:

- `tools/heater_retire_production_common.py` — exact source/version checks,
  one-shot authorization marker, Docker/runtime probes, privacy-safe state
  checks, and bounded restart readiness.
- `tools/heater_retire_production_preflight.py` — exact hardened dry-run
  validation, private candidate materialization, persistent rollback retention,
  and Scheduler invariants.
- `tools/run_heater_retire_production_gate.py` — the fail-closed production
  orchestrator and automatic rollback state machine.
- `ops/run-heater-retire-production-gate.sh` — a thin launcher only.

The public source never contains the private heater target, secret values,
private host config path, schedule names, schedule times, weekdays, or raw
private Home Assistant configuration.

## Authorization boundary

The gate is unusable without all of the following:

1. exact final reviewed source SHA, tree, parent and Home Assistant version;
2. a full fresh hardened RETIRE dry-run PASS from that same source;
3. an exact source-bound one-shot owner authorization phrase;
4. a not-yet-consumed persistent authorization marker.

The phrase format is source-bound by the gate code, but no usable final phrase
is declared in this document. A usable phrase may be formulated only after the
gate PR is merged, the final merged source is pinned, and another fresh exact
hardened dry-run passes from that final source.

Authorization is consumed with an `O_EXCL` persistent marker immediately before
the first bounded live write. Reuse of the same source-bound authorization
fails closed.

Previously consumed production authorizations remain consumed forever.

## Prewrite gate

Before any live Home Assistant config write, the orchestrator must:

1. pass exact git SHA/tree/parent checks;
2. match the repository Home Assistant version and running Home Assistant
   version;
3. run the canonical hardened RETIRE dry-run and validate its complete PASS
   shape;
4. prove Recorder-backed runtime safety:
   - legacy schedule helper is not explicitly `on`;
   - exactly two legacy scheduling identities are observable before the write;
   - zero legacy scheduling automations are explicitly enabled;
   - the preserved timer helper has an observable `on` or `off` state;
5. discover the single Docker mount whose destination is `/config` without
   emitting the private host source path;
6. pass full live Home Assistant `check_config`;
7. privately materialize the two reviewed RETIRE candidate files inside the
   Home Assistant container using the installed Home Assistant YAML loader;
8. rerun the exact hardened RETIRE dry-run immediately before rollback
   retention and the live-write boundary;
9. require Scheduler storage to be valid with an empty `data.schedules` list;
10. retain and fsync private rollback material containing both bounded original
    files, the prewrite Scheduler snapshot and metadata needed to preserve file
    ownership/mode;
11. prove the retained Scheduler snapshot still matches live Scheduler bytes
    exactly.

Retained rollback material is not automatically deleted.

## Authorized apply

After the one-shot marker is consumed, exactly two live config files may be
changed. Each replacement uses the already-reviewed verified atomic replacement
primitive and must end as:

- `equals_candidate`;
- parent-directory fsync performed.

No Scheduler service call, Scheduler storage write, helper toggle, automation
toggle, heater actuation, or Home Assistant reload is part of this gate.

After both bounded writes, the gate requires:

1. full live Home Assistant `check_config`;
2. Scheduler storage byte-for-byte identity with the prewrite snapshot;
3. a Home Assistant restart;
4. a changed container `StartedAt` boundary;
5. exact running Home Assistant version;
6. Home Assistant TCP listener readiness;
7. two consecutive restart-aware Scheduler semantic PASS observations proving
   empty recurring schedules remain empty;
8. the preserved timer helper state matches its prewrite `on`/`off` state;
9. no legacy scheduling automation is explicitly enabled;
10. the legacy scheduling helper is not explicitly `on`.

Raw Scheduler byte equality after restart is evidence only. For the proven
empty-to-empty RETIRE case it is not the semantic pass requirement.

The gate explicitly does **not** claim exact full Home Assistant state-machine
equivalence across restart.

## Automatic rollback

Any exception or failed invariant after a bounded target may have changed enters
automatic rollback. Unexpected runtime errors are treated the same way as
declared gate failures.

Rollback must:

1. restore every changed bounded target with the verified rollback primitive;
2. require `equals_original` and parent-directory fsync;
3. verify both bounded originals including content, mode, uid and gid;
4. pass full live Home Assistant `check_config`;
5. if a candidate restart was attempted, restart again after restoring the
   originals — even when the original restart command itself failed or only
   partially completed;
6. verify the same empty-to-empty Scheduler semantic invariant after that
   restart, or exact Scheduler byte identity when no restart was attempted;
7. require the preserved timer helper state to match prewrite;
8. require zero explicitly enabled legacy scheduling automations and the legacy
   helper not explicitly `on`.

Scheduler storage is never rewritten during rollback.

If any rollback verification fails, the public decision is
`PRODUCTION_INCIDENT_ROLLBACK_FAILED`. The operator must stop; this gate does
not improvise a second write path.

## Public decisions

The gate emits only sanitized status:

- `PRODUCTION_RETIRE_APPLY_COMPLETE` — candidate loaded and all post-restart
  invariants passed.
- `PRODUCTION_APPLY_FAILED_ROLLBACK_COMPLETE` — apply failed, exact bounded
  originals were restored, and rollback invariants passed.
- `PRODUCTION_INCIDENT_ROLLBACK_FAILED` — rollback could not be fully verified.
- `BLOCKED` — no bounded production config change occurred.

A consumed authorization may still be reported with `BLOCKED` when the marker
was consumed but the first bounded replacement failed before changing its
target. Such authorization is not reusable.

## Final operator flow

After this source is merged:

1. pin the new merged SHA/tree/parent and verify its signature;
2. run a fresh hardened RETIRE dry-run from that exact merged source;
3. only if it returns
   `READY_FOR_NEW_EXPLICIT_RETIRE_PATH_PRODUCTION_APPLY_GATE`, formulate the new
   exact one-shot authorization phrase;
4. require the owner to send that phrase exactly;
5. only then construct the final operator command that supplies the exact
   source tuple, Home Assistant version, selected container, authorization
   phrase, `--execute`, and `--retire`.

A merge, this document, a prior dry-run PASS, or `turpini` never authorizes the
production execution.
