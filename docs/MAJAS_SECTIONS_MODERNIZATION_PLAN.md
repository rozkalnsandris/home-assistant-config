# Mājas bounded Sections modernization plan

Phase 3C is a source-only design gate for the active modular Mājas dashboard.

The live Phase 3B audit established a narrow, sanitized baseline:

- the active view already uses native Home Assistant Sections;
- structure is 1 view / 3 sections / 12 recursive cards / 11 custom cards / 1 distinct custom-card type;
- there are 8 top-level cards;
- exactly one top-level grid wrapper contains 4 nested custom cards;
- there are no top-level stack wrappers;
- top-level custom cards do not currently declare explicit `grid_options`, so custom-card Sections sizing remains a separate uncertainty.

## Bounded proposal

The planner models exactly one transformation in memory:

1. locate the single qualifying top-level grid card;
2. require exactly 4 child card mappings;
3. require all 4 children to be custom cards;
4. replace the wrapper at its current list position with those 4 children in the same order;
5. leave every child mapping unchanged;
6. leave every non-target view, section and card mapping unchanged;
7. do not add, remove or modify `grid_options`.

The planned recursive structure therefore changes only because the non-custom grouping wrapper disappears:

- before: 1 / 3 / 12 / 11 / 1;
- proposed: 1 / 3 / 11 / 11 / 1;
- top-level cards: 8 -> 11;
- top-level grouping wrappers: 1 -> 0.

This is intentionally a layout modernization, so visual placement is expected to change. The source planner only proves that card configuration payloads and ordering are preserved. It does not claim that a custom card's runtime sizing or rendering is unchanged.

## Fail-closed boundaries

The planner blocks if the live/source evidence drifts, including:

- baseline structure drift;
- non-Sections view;
- grouping-wrapper count/shape drift;
- unsupported keys on the grid wrapper;
- child count/type drift;
- proposed structure drift;
- non-target payload drift;
- `grid_options` count drift;
- Home Assistant version mismatch.

Home Assistant documents the grid card keys used by this gate as `type`, optional `title`, optional `square`, optional `columns`, and required `cards`. A non-empty wrapper title is not silently discarded: the planner returns `NEEDS_PRIVATE_REVIEW`.

## Privacy and mutation

Public output is limited to decision/reason codes, ordinal target positions, counts and booleans. It must not emit raw YAML, private paths, entity IDs, titles, card/custom-card type names, actions, URLs or binding values.

The planner is inert unless `--plan` is supplied. It performs no dashboard/config write, binding change, `.storage` write, candidate or old-source modification, reload or restart.

Successful source decision:

`READY_FOR_PRIVATE_SECTIONS_FLATTENING_DRY_RUN`

This decision authorizes no live change. A later private dry-run must separately prove the exact active target and no-write invariants before any owner-gated production apply is considered.
