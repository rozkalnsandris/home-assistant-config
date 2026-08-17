# Mājas Sections modernization audit

This phase answers one question before any UI/layout change: **does the live modular dashboard actually need a bounded Sections modernization change?**

The audit is intentionally read-only and private-safe. It operates on the active modular dashboard only after the Phase 2 split/cutover has been verified stable.

## Why audit first

Current Home Assistant guidance treats Sections as the default responsive view layout. Cards in a Sections view can use layout/grid sizing, while cards nested inside grouping cards such as Grid or horizontal/vertical stacks do not get the same direct Layout/Visibility controls. Custom cards can provide runtime grid sizing that is not inferable from YAML alone.

For that reason the audit distinguishes:

- a concrete structural modernization signal, such as a non-Sections view or a grouping layout wrapper;
- invalid/ambiguous YAML layout declarations that require private review;
- custom-card default sizing where runtime capability cannot be inferred from YAML;
- an already-modern layout where no source change is justified.

Official references:

- https://www.home-assistant.io/dashboards/sections/
- https://www.home-assistant.io/dashboards/cards
- https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/

## Decisions

- `SECTIONS_ALREADY_MODERN_NO_ACTION`
- `READY_FOR_BOUNDED_SECTIONS_MODERNIZATION_DESIGN`
- `NEEDS_PRIVATE_REVIEW`
- `BLOCKED`

`READY_FOR_BOUNDED_SECTIONS_MODERNIZATION_DESIGN` does **not** authorize a live dashboard edit. It only means the sanitized structure contains an evidence-backed layout wrapper or non-Sections view worth designing a bounded candidate for.

`NEEDS_PRIVATE_REVIEW` is deliberately used for uncertainty, especially custom-card sizing that may be supplied at runtime rather than in YAML.

## Sanitized output

The report may include only structural/layout information such as:

- exact Home Assistant version match;
- safe owner-kind enum;
- dashboard structural counts;
- whether all views are Sections;
- anonymous `view_00` / `section_00` labels;
- max-column and dense-placement classifications;
- top-level card/custom/grid/stack counts;
- explicit/default grid-options counts;
- full/bounded/unspecified/invalid width classification counts;
- grouping-wrapper nested-card counts;
- custom-card explicit/default grid-options coverage.

The report never emits:

- raw dashboard YAML;
- exact private paths;
- entity IDs;
- view, section, or card titles;
- card type names;
- custom-card type names;
- actions, URLs, schedules, media/camera/presence data, or household metadata.

## Safety boundary

The tool is source/read-only tooling.

It does not write the dashboard or owner file, change the live binding, mutate the candidate or old source, touch `.storage`, reload Home Assistant, restart Home Assistant, or delete rollback material.

The CLI is inert unless `--audit` is supplied. Even with `--audit`, the implementation remains read-only.

**Production deploy/change: NO.**
