# Mājas dashboard 2026.9 quality audit

Tracking issue: `#148`.

This document defines the current privacy-safe audit contract for the accepted
modular `Mājas YAML` dashboard on Home Assistant `2026.9.3`.

The real dashboard remains private. Public tooling may report only sanitized
counts, booleans, enums and decision codes. It must never emit raw dashboard
YAML, household entity/helper/device identifiers, private paths, titles,
targets, URLs, schedules, media/presence/camera data or unnecessary runtime
topology.

## Current source/runtime baseline

The current repository pin is `2026.9.3`.

The accepted post-roadmap dashboard shape remains:

- one view;
- three sections;
- 11 recursive/top-level cards;
- 11 custom cards;
- one distinct custom-card type;
- zero grouping wrappers;
- the exact five-file / three-directory modular tree;
- native Sections for every view.

Historical migration tooling keeps its historical constants. In particular,
pre-flattening tools that modeled the older 12-card shape are not rewritten to
pretend that historical evidence was produced against the current layout.

The pre-2026.9 audit implementation is retained as
`tools/audit_majas_dashboard_quality_legacy.py`. The current entry point is:

```text
tools/audit_majas_dashboard_quality.py
```

## Current guidance represented

The current audit and planner are aligned with the upstream contracts relevant
to issue `#148`:

- YAML dashboards:
  https://www.home-assistant.io/dashboards/dashboards/
- Sections:
  https://www.home-assistant.io/dashboards/sections/
- dashboard actions:
  https://www.home-assistant.io/dashboards/actions/
- YAML includes:
  https://www.home-assistant.io/docs/configuration/splitting_configuration/
- custom-card Sections grid API:
  https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/
- button-card Sections mode:
  https://github.com/custom-cards/button-card/blob/master/docs/source/advanced/section-views.md
- button-card actions:
  https://custom-cards.github.io/button-card/dev/config/actions/
- button-card releases:
  https://github.com/custom-cards/button-card/releases
- Browser Mod popup browser-call model:
  https://github.com/thomasloven/hass-browser_mod/blob/master/documentation/popups.md

The audit does not turn upstream recommendations into automatic rewrites.
Installed capability and exact semantic equivalence must be positively proven.

## 2026.9 classifications

In addition to the established structure/layout/action-safety metrics, the
current audit classifies:

- top-level custom-card count;
- `section_mode` true / false / missing / invalid counts;
- explicit/default/invalid `grid_options`;
- shared-template root fixed height/width/aspect-ratio conflicts;
- dead `triggers_update` declaration count;
- action schema classes:
  `perform_action`, legacy service action, integration event,
  informational, navigation, other state-changing and unknown;
- legacy top-level Lovelace mode presence/class;
- installed custom-card Sections sizing capability as
  `proven`, `unknown` or `unavailable`;
- installed `triggers_update` runtime token as `present`, `absent` or `unknown`.

No dependency source path or private dashboard scalar is emitted.

Malformed `section_mode`, invalid `grid_options`, version drift, unexpected
modular tree state or other fail-closed baseline violations return `BLOCKED`.

## Sections sizing rule

Current custom-card guidance exposes a Sections grid API through
`getGridOptions()`. The installed dashboard custom-card bundle is checked
privately for the corresponding Sections capability.

The current planner may enable `section_mode: true` only when that installed
capability is positively proven.

It does not guess `grid_options` from screenshots. Issue `#148` intentionally
records `grid_options_added_count = 0` unless a future separately reviewed
scope proves exact responsive dimensions.

Fixed root `height`, `width` or aspect-ratio styling is treated as conflicting
with Sections-aware automatic sizing. A fixed dimension may be removed only
when the affected shared template is proven to be used exclusively by the
target top-level card set. Reuse outside that set fails closed for private
review.

## Dead configuration rule

A `triggers_update` declaration is removable only when:

1. the active private dashboard contains the declaration; and
2. the installed current custom-card bundle proves the runtime token absent.

If installed behavior is unavailable or ambiguous, the planner stops rather
than treating the declaration as dead.

## Action modernization rule

The current Home Assistant dashboard action model uses `perform-action`.
Legacy button-card `call-service` actions may be converted in memory only when
the mapping has an unambiguous service and no conflicting new-schema keys.

The bounded transformation is structural:

- `action: call-service` -> `action: perform-action`;
- `service` -> `perform_action`;
- `service_data` -> `data`;
- all unrelated fields and private values are preserved in memory.

Browser Mod `fire-dom-event` actions are intentionally excluded from that
generic conversion and remain unchanged.

## Lovelace legacy-mode rule

The repository's neutral validation fixture no longer sets top-level
`lovelace.mode: storage`; Home Assistant's default storage behavior plus named
YAML dashboard registrations is validated against exact `2026.9.3`.

For the private planner, top-level legacy `mode: storage` may be removed only
when the dashboard registry is present and every registered dashboard in that
bounded mapping has an explicit YAML mode and filename. Any other legacy mode
shape fails closed.

Removing the legacy mode never changes dashboard filename/binding values.

## Private in-memory planner

Current planner:

```text
tools/plan_majas_dashboard_2026_9.py
```

It requires the accepted post-roadmap structure and operates on deep copies of
the privately loaded dashboard/Lovelace mappings.

Supported bounded delta classes are only:

1. enable Sections mode on the exact top-level custom-card set;
2. remove proven conflicting fixed root dimensions from exclusively targeted
   shared templates;
3. remove proven-dead `triggers_update`;
4. normalize provably equivalent legacy service actions;
5. remove the proven-redundant top-level Lovelace storage mode.

The planner never adds `grid_options`, changes bindings, changes targets,
changes titles, or writes a live file.

Its public `planned_delta` contains counts only.

## Decisions

Audit decisions remain compatible with the post-roadmap quality gate and may
surface bounded candidate classes.

The 2026.9 planner uses:

- `DASHBOARD_2026_9_CURRENTLY_ALIGNED_NO_CHANGE`
- `READY_FOR_BOUNDED_MAJAS_2026_9_APPLY`
- `NEEDS_PRIVATE_REVIEW`
- `BLOCKED`

A source-only in-memory candidate remains `NEEDS_PRIVATE_REVIEW` until a
separate private full candidate validation has passed against exact Home
Assistant `2026.9.3`.

`READY_FOR_BOUNDED_MAJAS_2026_9_APPLY` is not production authorization.

## Execution

Read-only audit:

```text
python -m tools.audit_majas_dashboard_quality --audit --stdout
```

Read-only planner:

```text
python -m tools.plan_majas_dashboard_2026_9 --plan --stdout
```

Both commands are inert without their explicit gate flag and perform no live
dashboard/config write, binding change, `.storage` mutation, helper/entity/
device mutation, reload/restart or host/runtime mutation.

A later private validation harness may use the planner's in-memory candidate in
a temporary configuration copy. Raw private candidate YAML must never enter
GitHub comments, CI output or artifacts.

## Production boundary

Issue `#148` source/merge authority does not grant production authority.

If the validated planner result is
`READY_FOR_BOUNDED_MAJAS_2026_9_APPLY`, stop at the separate production
authorization gate. Any live dashboard/config mutation must be tied to the
exact merged source, exact current runtime baseline, exact bounded candidate
classes, limits, exclusions and verification plan.

Production deploy/change from this source audit/planner implementation:
**NO — source/tooling only until a separately authorized live apply.**
