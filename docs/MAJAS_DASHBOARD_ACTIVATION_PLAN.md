# Mājas dashboard activation planning gate

Issue: #39  
Parent roadmap: #26  
Reviewed baseline: `a50c5a8f9040fdfdd8b2fcea4da6ae78712a5fe0`  
Expected Home Assistant: `2026.8.2`

## Purpose

`tools/plan_majas_dashboard_activation.py` is a **read-only activation
planner** for the already validated private side-by-side Mājas candidate.

It never writes the Home Assistant configuration. Its job is to prove that a
future activation can be reduced to one narrowly bounded `filename` scalar
change with an exact byte-for-byte rollback payload available before any live
write is considered.

## Home Assistant model

Official references:

- https://www.home-assistant.io/dashboards/dashboards/
- https://www.home-assistant.io/docs/tools/dev-tools/

Home Assistant registers YAML dashboards below `lovelace: dashboards:` and
binds each dashboard definition to a YAML file with `filename`.

Configuration changes require a supported reload path or a Home Assistant
restart. This planner does not assume a hot reload exists. Any eventual
binding write and any restart are separate owner-authorized gates.

## Private-safe plan

At runtime the planner accepts:

- the private Home Assistant configuration root;
- the target dashboard title;
- the already validated private candidate root;
- the exact expected Home Assistant version.

It then:

1. resolves whether the dashboard registry is owned directly by
   `configuration.yaml` or by one `lovelace: !include` file;
2. resolves exactly one dashboard definition by title;
3. verifies the current source file remains bounded under the configuration
   root;
4. verifies the private candidate is bounded and still has exactly five YAML
   files and the reviewed directory shape;
5. verifies active and candidate payloads still match the reviewed
   1 view / 3 sections / 12 cards / 11 custom cards /
   1 distinct custom-card-type baseline;
6. verifies active and candidate payloads are semantically equivalent;
7. validates the candidate with the exact Home Assistant version and its own
   YAML loader;
8. locates the target `filename` scalar using PyYAML node source positions;
9. constructs an **in-memory only** proposed scalar replacement;
10. proves the bytes before and after that scalar are unchanged;
11. proves the proposed YAML changes only the intended filename field
    semantically;
12. retains the exact original owner-file bytes in memory as the future
    rollback payload.

The report emits only owner classification, reviewed counts, booleans and
decision/reason codes. It does not emit paths, filename values, entity IDs,
view metadata, card titles, custom-card type names or raw private YAML.

## Byte-preserving patch

The future activation design deliberately does **not** re-serialize the whole
owner YAML file. Re-serialization can alter comments, quoting and formatting.

Instead, the planner uses the parsed YAML scalar node's source span and builds
a surgical replacement for only the target `filename` scalar. Quoting style is
preserved when the existing scalar is single- or double-quoted. Byte offsets
are calculated from UTF-8 prefixes so non-ASCII text before the scalar remains
safe.

## Success decision

The source planner succeeds with:

`READY_FOR_PRIVATE_ACTIVATION_DRY_RUN`

Success requires:

- exact candidate tree=true;
- active/candidate equivalence=true;
- unique target binding patch=true;
- non-target bytes preserved=true;
- non-target semantics preserved=true;
- exact original rollback bytes captured in memory=true;
- exact Home Assistant version match=true;
- Home Assistant candidate parse=true;
- all mutation flags=false.

## CLI gate

The CLI is inert unless `--plan` is explicitly supplied.

Even with `--plan`, execution is read-only. It creates no backup, writes no
configuration, changes no dashboard binding, touches no `.storage` state and
performs no reload or restart.

## Later gates

After this source is reviewed and merged, a separate private live dry-run may
bind the planner to the real candidate and produce sanitized evidence.

Only after that may a separately authorized production gate consider:

1. capturing the rollback payload privately;
2. applying the one-scalar binding patch;
3. validating the written configuration;
4. separately authorizing the required Home Assistant restart/reload;
5. verifying LAN and remote behavior;
6. rolling back immediately if verification fails.

**Production deploy/cutover: NO — source and planning only.**
