# Heater scheduling ownership

Issue #9 tracks consolidation of two Home Assistant heater scheduling paths that could target the same switch.

## Intended authority

The public source candidate uses one scheduling authority:

- `packages/heater_scheduler.yaml` owns recurring scheduled ON/OFF actions through the Scheduler integration (`scheduler.add`).
- `packages/silditajs.yaml` keeps only the independent auto-off timer behavior.

The legacy direct time-triggered ON/OFF automations and their dedicated schedule helpers are removed from the source candidate so they cannot become a second schedule authority after a future reviewed production sync.

## Preserved timer behavior

`packages/silditajs.yaml` still provides:

- `input_boolean.silditajs_taimeris`;
- `input_number.silditajs_taimeris_min`;
- automation `silditajs_auto_off`.

When the heater switch turns on and the timer helper is enabled, the automation waits for the configured number of minutes, confirms the heater is still on, and then turns it off.

## Production gate

This change is source-only. It does not prove that the legacy direct schedule is unused in the current live instance.

Before any production apply, the operator must privately verify:

1. the live state of the legacy direct-schedule helper/automations;
2. the existing Scheduler entries that control the heater;
3. current dashboard usage still relies on the `heater_sched_*` controls while the timer remains required;
4. the full assembled production candidate against the exact Home Assistant version;
5. backup and rollback readiness.

No live Home Assistant write, reload or restart is authorized by this document or its PR.
