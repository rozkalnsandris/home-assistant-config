# FAST-LANE v2.1 Hybrid — Home Assistant config

This public repository adopts the shared FAST/STRICT workflow without weakening public-information or live Home Assistant safety.

## FAST

FAST includes Git-only documentation, reviewed YAML/config source and tests when no live `/config`, device, helper, automation or container state is changed. A FAST batch may cover 2-5 closely related same-risk configuration items and may proceed through Ready with up to two scope-preserving corrective commits.

## STRICT

Separate explicit owner authorization is required for live `/config` writes, reload/restart/recreate, Home Assistant service calls that mutate state/devices, `.storage`, backup operations, Cloudflare/ingress, Docker/systemd/host changes, secrets or other live mutation.

## CI and evidence

The existing validation workflow remains intact in Phase 1 because every public config change still needs secret/public-metadata and Home Assistant validation. Speed comes from batching and reduced authorization/evidence ceremony rather than skipping protective validation.

Produce one complete Ready receipt; immediately before merge refresh only mutable merge evidence. Merge remains explicit and never authorizes production application.
