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

## Historical live scheduling audit

`tools/audit_heater_scheduling_live.py` is retained as a privacy-safe historical/diagnostic audit for the earlier scheduling-ownership investigation. It reports the older pre-transition shape in which Scheduler heater entries could already exist.

It is **not** the canonical readiness gate for the post-#112 RETIRE path. In particular, its older readiness policy expects matching/enabled Scheduler heater entries, while the proven RETIRE baseline requires Scheduler storage to be valid with an empty `data.schedules` list.

The historical audit remains read-only and privacy-safe, but its `READY_FOR_PRIVATE_PRODUCTION_APPLY_PREPARATION`, `NEEDS_REVIEW`, or `BLOCKED` decision must not be used to authorize or reject the hardened RETIRE production gate by itself.

## Canonical RETIRE preflight

For the post-#112 RETIRE path, the canonical pre-production evidence is the hardened dry-run:

```bash
python -B tools/run_heater_retire_hardened_dry_run.py \
  --docker docker \
  --container <selected-home-assistant-container> \
  --expected-sha <exact-reviewed-main-sha> \
  --expected-tree <exact-reviewed-main-tree> \
  --expected-parent <exact-reviewed-main-parent> \
  --expected-version <exact-home-assistant-version> \
  --dry-run \
  --retire
```

The operator-facing wrapper around this command must independently pin the exact source SHA/tree/parent before invoking it. The canonical dry-run uses the post-incident reconciliation policy and must end with:

`READY_FOR_NEW_EXPLICIT_RETIRE_PATH_PRODUCTION_APPLY_GATE`

A valid RETIRE preflight requires all of the following:

1. exact source SHA/tree/parent and Home Assistant version gates pass;
2. the legacy scheduling source shape is still exact enough to prove the old ON/OFF automations were gated by the legacy schedule helper;
3. the legacy schedule helper is not explicitly `on`; post-restart Recorder states `off`, `unknown`, or `unavailable` are accepted only with the exact source gate proof;
4. exactly two legacy scheduling automation identities remain observable and zero are explicitly enabled;
5. the preserved auto-off timer source remains present;
6. private binding resolution proves the legacy ON target, legacy OFF target, Scheduler target, and timer target are equal without emitting the private target;
7. Scheduler storage is valid and `data.schedules` is an empty list;
8. non-package dashboard YAML references the new scheduling controls and timer but not the removed legacy scheduling controls;
9. latent legacy ON/OFF time validity is evidence only for RETIRE and is not a readiness requirement because those helpers are removed by the RETIRE candidate;
10. all privacy and mutation claims remain false.

The hardened dry-run then privately materializes the reviewed candidate, exercises both bounded file replacements on a temporary config copy, requires two `equals_candidate` classifications with parent-directory fsync, runs full Home Assistant `check_config`, rolls both temporary files back with two `equals_original` classifications and parent-directory fsync, and proves the live bounded files and live Scheduler storage were not changed.

Because the dry-run has no authorization to mutate production, live Scheduler storage must remain byte-for-byte unchanged throughout the dry-run as well as semantically empty.

Any helper state explicitly `on`, any explicitly enabled legacy scheduling automation, nonempty or invalid Scheduler storage, source/dashboard drift, private binding mismatch, Home Assistant version mismatch, runtime drift, temporary apply/rollback verification failure, `check_config` failure, privacy failure, or live mutation evidence fails closed.

## Post-restart and rollback Scheduler invariant

Production restart/rollback verification must distinguish bounded file integrity from Scheduler storage serialization.

For the two bounded heater YAML files, exact byte classification and parent-directory fsync verification remain mandatory during apply and rollback.

For Scheduler storage after a Home Assistant restart, raw `.storage` byte identity is evidence but is not the sole pass/fail invariant. Home Assistant storage can perform delayed or final writes around shutdown/startup, so an empty recurring-schedule state may be reserialized without creating recurring heater behavior.

Use `tools/verify_scheduler_storage_semantics.py` against the retained prewrite Scheduler snapshot and the current Scheduler storage. This verifier is deliberately scoped to the RETIRE path whose proven prewrite recurring schedule list is empty. The gate behaves as follows:

1. both storage documents must be valid and contain a list-valued `data.schedules` field;
2. the retained prewrite recurring schedule list must be empty;
3. the current post-restart recurring schedule list must also be empty;
4. raw byte equality and full parsed-JSON equality are reported as additional evidence but are not required for the empty-to-empty PASS case;
5. malformed, missing, oversized, symlinked, or otherwise invalid storage fails closed;
6. any nonempty prewrite schedule list fails closed with `NONEMPTY_PREWRITE_REQUIRES_RESTART_AWARE_VERIFICATION` instead of claiming semantic equivalence from schedules alone.

The nonempty restriction matters because Scheduler persists a shutdown timestamp and uses it after restart when deciding whether an already-overlapping timeslot action should execute again or be skipped. A future verifier for nonempty Scheduler state must therefore include restart-aware execution semantics rather than only compare `data.schedules`.

This semantic relaxation applies only across restart/rollback boundaries for the proven empty Scheduler baseline. The hardened dry-run still requires live Scheduler storage to remain byte-for-byte unchanged because the dry-run is not authorized to mutate it at all.

## Production gate

A successful hardened RETIRE dry-run does not authorize a production sync by itself.

Before any production apply, the operator must additionally:

1. pin the exact final reviewed source SHA/tree/parent and exact Home Assistant version;
2. rerun the hardened RETIRE dry-run immediately before the live-write boundary and require the full PASS decision;
3. capture and validate the prewrite Scheduler snapshot and require an empty recurring schedule list;
4. confirm bounded original-file rollback material is privately retained and verified before any live write;
5. require strict bounded apply verification, full live `check_config`, and Scheduler byte identity immediately before restart;
6. after an authorized restart, use the restart-aware Scheduler semantic verifier and require the recurring schedule list to remain empty;
7. predeclare and verify the exact rollback path, including bounded file restoration, parent-directory fsync, restored `check_config`, restart when required, and the same restart-aware Scheduler semantic verification;
8. obtain a new exact one-shot owner authorization for the final pinned production wrapper/gate.

No live Home Assistant write, Scheduler write/service/bootstrap, helper or automation toggle, heater actuation, reload, restart, or retained rollback cleanup is authorized by this document or by a dry-run PASS.
