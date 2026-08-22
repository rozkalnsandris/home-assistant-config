# Repository operating rules

This is a **public** repository for the explicitly reviewed, publishable subset of Home Assistant configuration source. Treat every change as both production-sensitive and public-information-sensitive even though GitHub Actions are validation-only.

## Source-of-truth boundary

- Track only declarative, reviewable Home Assistant source that has been explicitly classified as safe for public Git.
- Never commit secrets, runtime state, recorder/history databases, logs, backups, tokens, private keys, `.storage/`, `.cloud/`, credential-bearing URLs, or private media/camera content.
- Do not publish unnecessary private runtime coordinates, device identifiers, household metadata or topology simply because they are not credentials.
- `RPi5_main` owns the RPi5 host/runtime layer. Do not duplicate host ingress, systemd, Docker runtime, backup or privileged deployment ownership here.

## Change workflow

Use:

`issue → fresh branch → focused changes → Draft PR → CI → review → Ready → explicit owner squash merge → production-change classification`

Do not merge without explicit owner authorization.

After every merge state exactly one of:

- `Production deploy/change REQUIRED: YES`
- `Production deploy/change REQUIRED: NO`

A merge never authorizes production application by itself.

## Production boundary

Without separate explicit authorization, do not:

- write to the live Home Assistant `/config` directory;
- restart/recreate the Home Assistant container;
- invoke Home Assistant services that mutate devices, helpers, automations or runtime state;
- modify `.storage/` or UI-managed integration state;
- change Cloudflare/ingress, Docker/systemd ownership or host firewall state;
- create/delete Home Assistant backups;
- expose secret values or private runtime details in logs, issues, PRs or CI artifacts.

Prefer supported reload mechanisms for reloadable configuration. A Home Assistant restart is a separate production action when required.

## Live inventory

The first live interaction must be bounded and read-only. Inventory names/types/metadata only as needed to classify files. Never print the contents of `secrets.yaml`, `.storage/`, auth/token material, recorder DBs, private keys, or other excluded runtime data.

Before importing any live YAML into this public repository, review it for both credentials and unnecessary household/device metadata. Sanitize or keep it outside Git if publication is not required for the dashboard/configuration goal.

## Validation

Before any production apply:

1. repository policy check passes;
2. reachable Git-history secret scan passes;
3. YAML/static validation passes;
4. exact production Home Assistant version is known;
5. candidate configuration passes Home Assistant `check_config` against that exact intended version;
6. diff is reviewed for secrets/private-runtime leakage and public metadata exposure;
7. rollback revision and Home Assistant backup path are established;
8. production apply is separately authorized.

<!-- BEGIN FAST-LANE-V2.2-MANAGED -->
## FAST-LANE v2.2 Composite

Read `docs/FAST_LANE_V2_2.md` as the active local startup contract.

**Primary rule:** the human approves the **RISK / DECISION**; automation executes the **TECHNICAL STEPS**.

- `START`, `turpini`, or equivalent continuation may carry Git-only documentation/configuration/test work through Ready when no live Home Assistant state is changed. FAST does not reduce the public-information/secret review requirement.
- FAST may batch **2-5 closely related same-risk work items** and use up to **two scope-preserving corrective commits** for CI/review findings.
- Normal delivery has at most two owner gates: explicit **MERGE**, then one bounded **COMPOSITE LIVE** only when live Home Assistant/host mutation is required.
- Read-only inventory, validation, evidence refresh, CI/review inspection, candidate checks and reconciliation are technical steps, not owner gates.
- Composite Live must bind exact source SHA/config target, allowed mutation categories, practical limits, explicit exclusions and expected baseline. Preflight/revalidation belongs inside the same fail-closed one-shot.
- Authorization is consumed at the first authorized mutation. Any later error, ambiguity or drift requires evidence preservation and STOP; no automatic retry, rollback, cleanup or alternate mutation path unless explicitly pre-authorized.
- **STRICT** includes writing live `/config`, reload/restart/recreate, Home Assistant service calls that mutate runtime/devices, `.storage`, backups, Cloudflare/ingress, Docker/systemd/host mutation, secrets and equivalent live authority.
- Put any remaining owner decision visibly at the end under `ACTION REQUIRED` and provide exact copyable input when needed.
- Merge remains explicit owner authority and never authorizes production application.

Existing public-information and production-safety rules remain stricter where applicable.
<!-- END FAST-LANE-V2.2-MANAGED -->
