# Heater scheduling ownership

Issue #9 tracks consolidation of two Home Assistant heater scheduling paths that could target the same switch.

## Intended authority

The public source uses one scheduling authority:

- `packages/heater_scheduler.yaml` owns recurring scheduled ON/OFF actions through the Scheduler integration (`scheduler.add`).
- `packages/silditajs.yaml` keeps only the independent auto-off timer behavior.

The legacy direct time-triggered ON/OFF automations and their dedicated schedule helpers were removed from source so they cannot become a second schedule authority after a future reviewed production sync.

## Preserved timer behavior

`packages/silditajs.yaml` still provides:

- `input_boolean.silditajs_taimeris`;
- `input_number.silditajs_taimeris_min`;
- automation `silditajs_auto_off`.

When the heater switch turns on and the timer helper is enabled, the automation waits for the configured number of minutes, confirms the heater is still on, and then turns it off.

## Private-safe live preflight

Before any production apply, run the read-only live audit from an exact checked-out repository revision:

```bash
sudo python -B tools/audit_heater_scheduling_live.py --stdout
```

The audit reads live runtime evidence only inside the Home Assistant container and emits a sanitized report. It does not emit exact switch/entity targets, Scheduler names, schedule times, weekdays, dashboard paths/content, or secrets. It performs no Home Assistant, Scheduler, dashboard, container, Cloudflare, or host write/reload/restart.

A zero exit code requires `READY_FOR_PRIVATE_PRODUCTION_APPLY_PREPARATION`. The gate is intentionally conservative. It requires:

1. exact running Home Assistant version matches `home-assistant-version.txt`;
2. the legacy direct-schedule source is still present in live config before the sync;
3. the legacy schedule helper is currently `off` and its automations are observable;
4. the preserved timer helper state is observable;
5. Scheduler storage contains at least one enabled heater entry, uses a single exact target internally, and only contains the expected ON/OFF action shape for matching heater entries;
6. non-package YAML still references the newer `heater_sched_*` controls and the timer, but not the legacy direct-schedule controls;
7. all privacy guards remain false for private-value emission/read claims.

`BLOCKED` is used for a version mismatch, an active legacy schedule helper, a legacy dashboard reference, or a failed privacy guard. Other incomplete evidence returns `NEEDS_REVIEW` rather than guessing.

## Post-restart and rollback Scheduler invariant

Production restart/rollback verification must distinguish bounded file integrity from Scheduler storage serialization.

For the two bounded heater YAML files, exact byte classification and parent-directory fsync verification remain mandatory during apply and rollback.

For Scheduler storage after a Home Assistant restart, raw `.storage` byte identity is evidence but is not the sole pass/fail invariant. Home Assistant storage can perform delayed or final writes around shutdown/startup, so equivalent recurring-schedule state may be reserialized without changing heater behavior.

Use `tools/verify_scheduler_storage_semantics.py` against the retained prewrite Scheduler snapshot and the current Scheduler storage. The semantic gate fails closed unless both storage documents are valid and their parsed `data.schedules` lists are exactly equal. In particular:

1. if the prewrite recurring schedule list is empty, the post-restart list must also be empty;
2. if the prewrite list is nonempty, the post-restart parsed schedules must match exactly;
3. any schedule addition, deletion, enabled-state change, target/action change, or other parsed schedule difference blocks the gate;
4. raw byte equality and full parsed-JSON equality are reported as additional evidence but are not required when the recurring `data.schedules` semantics are preserved;
5. malformed, missing, oversized, symlinked, or otherwise invalid storage fails closed.

This semantic relaxation applies only across restart/rollback boundaries. The hardened dry-run still requires live Scheduler storage to remain byte-for-byte unchanged because the dry-run is not authorized to mutate it at all.

## Production gate

Source consolidation and a successful read-only live preflight still do not authorize a production sync by themselves.

Before any production apply, the operator must additionally:

1. review the sanitized live audit result against issue #9;
2. validate the full assembled production candidate against the exact Home Assistant version;
3. confirm backup and rollback readiness;
4. obtain separate explicit authorization for the exact live apply/reload/restart scope.

No live Home Assistant write, reload or restart is authorized by this document or its PR.
