# Mājas dashboard post-roadmap quality audit

Tracking issue: `#143`.

This document defines the current-state, privacy-safe audit contract for the
accepted modular `Mājas YAML` dashboard after the completed split and Sections
flattening roadmap.

The historical Phase 3 tool
`tools/audit_majas_sections_modernization.py` intentionally models the older
pre-flattening 12-card baseline and remains useful only for reproducing that
historical gate. It must not be treated as the current post-roadmap baseline.

The current audit is:

```text
tools/audit_majas_dashboard_quality.py
```

## Reviewed current baseline

The audit is deliberately fail-closed around the final accepted sanitized
shape recorded by the completed dashboard roadmap:

- Home Assistant `2026.8.3`;
- one view;
- three sections;
- 11 recursive cards;
- 11 custom cards;
- one distinct custom-card type;
- 11 top-level cards;
- zero grouping wrappers;
- the exact five-file / three-directory modular tree;
- native Sections for every view.

The Home Assistant version baseline follows the repository pin in
`home-assistant-version.txt`. Under #143 the pin moved from `2026.8.2` to
`2026.8.3` only after a sanitized read-only production probe proved exact
source/runtime version drift. The patch-level alignment does not itself imply a
dashboard redesign or any production mutation.

Changing this baseline requires a normal reviewed source change. Runtime drift
must not be made to pass by weakening the historical or current constants.

## Home Assistant guidance represented

The audit contract follows the current Home Assistant dashboard model:

- YAML dashboards:
  https://www.home-assistant.io/dashboards/dashboards/
- views:
  https://www.home-assistant.io/dashboards/views/
- Sections:
  https://www.home-assistant.io/dashboards/sections/
- cards:
  https://www.home-assistant.io/dashboards/cards/
- Heading:
  https://www.home-assistant.io/dashboards/heading/
- Tile:
  https://www.home-assistant.io/dashboards/tile/
- card features:
  https://www.home-assistant.io/dashboards/features
- actions:
  https://www.home-assistant.io/dashboards/actions/
- YAML includes:
  https://www.home-assistant.io/docs/configuration/splitting_configuration/
- custom-card Sections sizing API:
  https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/

Custom-card presence is not itself a defect. The audit never infers that a
custom card implements frontend `getGridOptions()` from YAML alone. Runtime
Sections sizing capability is therefore reported as `unknown` unless a later
separately reviewed mechanism positively proves it. `unknown` never authorizes
an automatic sizing rewrite.

Likewise, custom cards are not converted to native Heading/Tile/features merely
because native components exist. Semantic equivalence must be positively
proven; otherwise native replacement remains `unknown` and automatic
conversion remains false.

## Public-safe output

The tool emits counts, booleans, enums and anonymous view ordinals only.

It may report:

- expected/running Home Assistant version and match;
- public-safe dashboard binding owner kind;
- modular tree file/directory/symlink/unexpected counts;
- view/section/card/custom-card counts;
- all-views-Sections state;
- top-level card and grouping-wrapper counts;
- anonymous per-view `max_columns` and `dense_section_placement`
  classifications;
- explicit/default/invalid `grid_options` counts;
- full/bounded/default/unspecified width classifications;
- custom-card sizing capability as `unknown` or `unavailable`;
- native header/badge/Heading/Tile aggregate usage;
- native replacement eligible/unknown aggregate counts;
- action surfaces classified only as informational, navigation,
  state-changing or unknown;
- higher-impact state-changing aggregate counts only where a bounded service
  domain classification is possible in memory;
- confirmation/hold/double-tap coverage counts;
- candidate class and decision code.

It must never emit raw private YAML, private paths/bindings, entity IDs, titles,
custom-card identifiers, action targets/services/URLs, schedules, camera/media
or presence data.

## Decisions

The allowed successful decisions are:

- `DASHBOARD_CURRENTLY_OPTIMAL_NO_CHANGE`
- `READY_FOR_BOUNDED_DASHBOARD_QUALITY_PASS`

The fail-closed decision is:

- `BLOCKED` for version, tree, structure, layout declaration or runtime audit
  failure.

A successful current-state audit does not guess a visual redesign from a
screenshot. A bounded candidate is emitted only when the source can prove a
specific class without exposing private values. The initial supported bounded
class is `ACTION_SAFETY` for an unguarded higher-impact state-changing action
that can be classified privately.

Uncertain custom-card sizing or native-card equivalence is reported as
uncertainty, not as an automatic candidate.

## Execution

The CLI is inert unless the explicit audit gate is present:

```text
python -m tools.audit_majas_dashboard_quality --audit --stdout
```

Run it only in a bounded read-only environment against the active Home
Assistant configuration. The tool itself performs no dashboard/config write,
binding change, `.storage` mutation, helper/entity/device mutation,
reload/restart or host/runtime mutation.

A sanitized `READY_FOR_BOUNDED_DASHBOARD_QUALITY_PASS` result still grants no
production authority. Any live dashboard change needs a separate precise owner
authorization bound to the exact reviewed source, target and baseline.

Production deploy/change from this source audit tooling: **NO**.
