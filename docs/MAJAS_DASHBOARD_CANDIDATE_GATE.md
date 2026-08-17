# Mājas YAML Phase 2C candidate gate

Issue #34 is the source-only gate for creating a private side-by-side split candidate after the read-only equivalence proof in #33.

Official Home Assistant references:

- nested `!include*` and directory include behavior: https://www.home-assistant.io/docs/configuration/splitting_configuration/
- configuration validation without restart: https://www.home-assistant.io/docs/tools/check_config/
- exact Home Assistant 2026.8.2 YAML loader is the runtime validation target.

## Candidate shape

The reviewed private candidate shape is:

```text
<private-candidate-root>/
├── dashboard.yaml
├── views/
│   └── 00_view.yaml
└── sections/
    └── view_00/
        ├── 00_section.yaml
        ├── 10_section.yaml
        └── 20_section.yaml
```

`views/` and `sections/` remain sibling directories because `!include_dir_list` scans recursively. The view include therefore points from `views/00_view.yaml` to the sibling section directory.

## Materializer safety model

`tools/materialize_majas_dashboard_candidate.py` is deliberately inert unless `--materialize` is supplied. A later live run must also provide an explicit destination.

The tool fails closed unless all of these conditions hold:

- the active private dashboard resolves uniquely inside `/config`;
- the reviewed shape remains exactly 1 view / 3 sections / 12 cards / 11 custom cards / 1 custom-card type;
- the requested destination resolves inside `/config`;
- the destination does not already exist;
- the destination parent already exists;
- the generated five-file tree round-trips to the same parsed dashboard object and structural counts;
- the running Home Assistant version is exactly `2026.8.2`;
- Home Assistant's own YAML loader resolves the generated candidate successfully.

The private payload is re-emitted with unknown Home Assistant YAML tags preserved. The public report contains only counts, booleans and decision/reason codes.

If any failure happens after the candidate root is created, cleanup is bounded to that newly-created candidate root. The active dashboard source, dashboard binding, `.storage`, reload state and Home Assistant process are not modified.

Successful later execution returns:

`READY_FOR_PRIVATE_CANDIDATE_REVIEW`

That result does not authorize a dashboard binding change, reload, restart or production cutover. Those remain separate owner-gated phases.

## Source-only boundary

Merging the Phase 2C tooling does not create any file in live Home Assistant `/config`. The real candidate materialization requires a fresh exact-SHA/version preflight and a separate explicit owner authorization.

**Production deploy/change: NO.**
