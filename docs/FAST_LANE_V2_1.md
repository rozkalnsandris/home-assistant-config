# FAST-LANE v2.2 Composite — Home Assistant config

> Compatibility path: `AGENTS.md` already points to this v2.1 filename; these are the authoritative v2.2 rules.

## Core rule

**The human approves the RISK / DECISION. Automation executes the TECHNICAL STEPS.** Read-only checks never create owner gates. STRICT classifies live mutation risk; it does not require approval for each technical checkpoint.

## FAST

Git-only documentation, reviewed YAML/config source and tests may proceed from fresh GitHub state through Ready in one coherent batch, including branch, PR, validation/review and up to two scope-preserving corrections. Batch 2-5 closely related same-risk items when coherent. Merge remains explicit.

## Human gate budget and Composite STRICT

Normal delivery has at most two owner gates: **MERGE**, then **COMPOSITE LIVE** only if live Home Assistant/host mutation is required. Before requesting live authority, gather all obtainable read-only evidence. One bounded authorization binds exact source SHA/config target, allowed mutation categories, limits, explicit exclusions and expected baseline. Preflight/revalidation is part of the same one-shot.

## Local STRICT boundaries

Live `/config` writes, reload/restart/recreate, state/device-mutating service calls, `.storage`, backup operations, Cloudflare/ingress, Docker/systemd/host changes, secrets or another live mutation require Composite Live authorization.

If live apply tooling produces a candidate/config artifact, use pinned tooling, verify the exact candidate and re-check runtime baseline/drift before applying it.

## Failure and evidence

Authorization is consumed at the first authorized mutation. Any later error/ambiguity requires evidence preservation and STOP; no automatic retry, rollback, cleanup or alternate mutation path unless explicitly pre-authorized.

Use one Ready receipt and one final live receipt. Put any remaining owner decision at the **end** under `ACTION REQUIRED`; when the owner must enter/run something, provide the exact copyable instruction in a fenced `bash` block.

Merge never authorizes production application or device/host mutation.
