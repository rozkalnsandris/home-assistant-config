# Heater RETIRE post-incident reconciliation gate

This document defines the source-only reconciliation policy used after the authorized #112 RETIRE attempt, its automatic rollback, and the #113 V8 proof that the two bounded heater files plus Scheduler storage were restored exactly.

## Why the historical planner is no longer sufficient

The historical planner was written for the pre-#112 state. It required:

- the legacy schedule helper to be exactly `off`;
- both legacy time values to remain valid in Recorder;
- both legacy schedule automation entities to be explicitly `on`.

After the #112 candidate restart and rollback restart, Recorder-backed entity continuity changed. A fresh privacy-safe snapshot on 2026-08-20 showed:

- two legacy automation identities are still present;
- zero legacy automations are explicitly enabled;
- zero are explicitly disabled;
- the legacy schedule helper is `unavailable`;
- Scheduler storage is valid and empty;
- Home Assistant remains on the expected version;
- #113 V8 already proved exact bounded-file restoration and exact prewrite Scheduler restoration.

Those restart-lifecycle states must not be mistaken for proof that recurring scheduling is active.

## Post-incident RETIRE policy

The canonical hardened RETIRE launcher installs a dedicated post-incident reconciliation policy. The historical planner remains unchanged for historical audit reproducibility.

The post-incident policy requires all of the following before a dry-run can continue:

1. Home Assistant running version exactly matches the repository version contract.
2. Recorder evidence is available.
3. The legacy schedule helper's latest Recorder state is one of `off`, `unknown`, or `unavailable`.
4. An explicit helper state of `on` fails closed.
5. Exactly two legacy schedule automation identities remain discoverable.
6. Zero legacy schedule automations are explicitly enabled; any enabled count greater than zero fails closed.
7. Live legacy source still has the exact daily ON/OFF semantics and the helper-state gate that prevents actions unless the helper is `on`.
8. The independent timer semantics remain present.
9. Scheduler source is present.
10. Scheduler storage is valid and contains zero recurring schedules.
11. No legacy dashboard reference remains; the new scheduling and timer references remain represented.
12. The reviewed RETIRE candidate removes the legacy helper/time helpers/direct automations, preserves the independent timer, and keeps the Scheduler save script.
13. The legacy ON target, legacy OFF target, Scheduler target, and timer target are privately resolved using Home Assistant's YAML/secret loader and proven equal without emitting or persisting the private values.
14. Privacy guards remain false and no production mutation occurs.

## Latent legacy times

The legacy time values are evidence only on the post-incident RETIRE path. Their validity is not a readiness requirement because RETIRE intentionally removes those helpers and does not preserve or bootstrap their old values. The #112 restart/rollback sequence also means Recorder continuity for those removed/recreated entities is not a reliable safety invariant.

This does **not** weaken the Scheduler invariant: Scheduler storage must still be valid and empty.

## Dry-run boundary

The post-incident reconciliation only permits the existing hardened dry-run to proceed. The dry-run still requires:

- exact source SHA/tree/parent/version gates;
- private candidate materialization in temporary storage only;
- verified two-file temporary apply with `equals_candidate` and parent fsync;
- full temporary Home Assistant `check_config`;
- verified temporary rollback with `equals_original` and parent fsync;
- live heater files unchanged;
- Scheduler storage byte-for-byte unchanged and semantically empty;
- Home Assistant runtime unchanged;
- no Scheduler service call/bootstrap;
- no live config write, helper mutation, heater actuation, reload, or restart.

A successful dry-run still does not authorize production execution. Production requires a separately reviewed wrapper/gate and a new exact one-shot owner authorization.

## Authorization ledger

- #99 is consumed forever.
- #112 is consumed forever.
- No production authorization is created by this policy or by its tests.

Production change: NO.
