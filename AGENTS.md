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

A valid current `AUTO-RUN FULL home-assistant-config #<issue>` command is one explicit issue-scoped owner squash-merge authorization for only the canonical PR implementing that frozen issue after its final exact-head readiness gate. Outside a valid FULL activation, use the ordinary explicit owner squash-merge gate.

After every merge state exactly one of:

- `Production deploy/change REQUIRED: YES`
- `Production deploy/change REQUIRED: NO`

A merge never authorizes production application by itself.

## Startup command routing

Read `.github/start-mode-routing.json` before selecting a startup/continuation mode.

- Bare `START`, `START home-assistant-config`, `turpini`, or equivalent continuation means normal **FAST-LANE v2.2**. FAST is the safe discovery/audit/non-FULL continuation lane; it does not activate `AUTO-RUN FULL`.
- For a concrete implementation issue with a usable Definition of Done, the preferred operator lane is the exact explicit form `AUTO-RUN FULL home-assistant-config #<issue>`.
- Activate `GITHUB-ONLY` only when the owner explicitly includes `GITHUB-ONLY` (or the documented `git hub only` spelling) in the current command.
- Activate `LIVE-ALL` only when the owner explicitly includes `LIVE-ALL` in the current command.
- Activate `AUTO-RUN FULL` only from the exact explicit form `AUTO-RUN FULL home-assistant-config #<issue>` and then read `.github/auto-run-full-v2.json` plus `docs/AUTO_RUN_FULL_V2.md` before any activation write.
- Never infer an explicit mode from `.github/start-github-only.json`, deploy-queue state, handoff/issue continuity, executor availability, historical chat mode, controller state, or a prior authorization receipt.
- A deploy queue, handoff, executor limitation or authorization receipt may affect the selected lane after routing, but it must never rewrite the command mode itself.

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
- Merge remains explicit owner authority and never authorizes production application. A valid `AUTO-RUN FULL home-assistant-config #<issue>` activation is a separate explicit owner decision with its own frozen source/merge envelope; it is never inferred from FAST continuation.

Existing public-information and production-safety rules remain stricter where applicable.
<!-- END FAST-LANE-V2.2-MANAGED -->

<!-- BEGIN AUTO-RUN-FULL-V2-MANAGED -->
## AUTO-RUN FULL v2

Canonical local contract: `.github/auto-run-full-v2.json` and `docs/AUTO_RUN_FULL_V2.md`. Durable controller state: issue `#140`. Adoption roadmap: issue `#141`.

- `AUTO-RUN FULL home-assistant-config #<issue>` is the normal implementation lane and one explicit, issue-specific owner decision for the frozen GitHub/source/merge envelope. It is not blanket repository authority and is never inferred from `START`, `turpini`, prior chat context, handoff continuity or controller state.
- Before activation, freshly read repository rules, the exact target issue/DoD, current `main`, active PR/CI/review/comment state, relevant handoff/dependencies and controller issue `#140`.
- Materialize the frozen issue-specific authorization as an owner-identity GitHub activation receipt before using FULL authority. Later issue edits never silently expand that frozen authority.
- Inside the frozen envelope, routine analysis/source/docs/config/tests, branch/commit/PR work, CI/review/fix convergence and ordinary merge-conflict corrections require no additional owner nudge.
- The FULL command is explicit squash-merge authority only for the canonical PR implementing that exact frozen issue and only after fresh final exact-head diff/scope/privacy review, required CI success, zero unresolved actionable review findings and fresh mergeability.
- GitHub native auto-merge may be used when repository capability is available. If unavailable, direct exact-head squash merge is only a fallback after the same readiness gate and only inside the frozen FULL merge authority. No ruleset bypass, force merge, reset/rebase/force-push or history rewrite is allowed.
- If the PR head changes, previous merge readiness is void. Freshly review/revalidate the new head before merge.
- After merge, verify exact `main` and classify exactly one: `Production deploy/change REQUIRED: YES` or `Production deploy/change REQUIRED: NO`.
- **AUTO-RUN FULL never grants Home Assistant production/live authority.** If production work is required by the target DoD, persist `PAUSED_PRODUCTION_AUTH` and require a separate precise current owner authorization before the first live mutation.
- Separate production authorization must bind the exact source/config revision, exact target/action, allowed mutation class, practical limits, explicit exclusions, expected baseline and recovery semantics. Merge authorization is never production authorization.
- Live `/config` writes, reload/restart/recreate, Scheduler/UI-managed state mutation, helper/entity/device mutation or actuation, `.storage`, backups, secrets/credentials, databases, host/runtime/infrastructure and arbitrary SSH/shell authority are never implied by FULL.
- After any separately authorized live mutation begins, error, ambiguity or unexpected drift requires public-safe evidence preservation and `STOP_ERROR`; no automatic retry, rollback, cleanup or alternate mutation path unless explicitly pre-authorized.
- Controller/receipt state in this public repository must never expose private household YAML, exact household entity/device/config-entry IDs, secret aliases/values, private paths/targets, schedule values, private URLs, camera/media/presence data or other unnecessary private runtime information.
- A ChatGPT turn/session ending, CI wait or review wait is resumable state, not a STOP. Persist continuity in GitHub; configured event-triggered Work, an hourly watchdog or manual `turpini` may resume only the already-active frozen issue.
- If ChatGPT/app permissions require a product-level confirmation, persist `PAUSED_PLATFORM_APPROVAL`; repository policy cannot suppress a platform-mandated approval.
- Provider LLM API keys, token-billed fallback and automatic paid-credit purchase are forbidden by default.
- Three materially identical failed attempts without a materially new safe hypothesis produce `STOP_ERROR`; do not loop blindly.
- Normal terminal state is `DONE` only after the target DoD is proven, exact post-merge `main` is verified, production-change classification is recorded, any separately authorized production work required by the issue is verified, a final public-safe receipt is written and controller `#140` returns to `IDLE`.
<!-- END AUTO-RUN-FULL-V2-MANAGED -->

<!-- BEGIN GITHUB-ONLY-LIVE-ALL-V1-MANAGED -->
## GITHUB-ONLY / LIVE-ALL v1

Canonical shared contract: `rozkalnsandris/ops-workflows/docs/GITHUB_ONLY_LIVE_ALL.md` with machine invariants in `policy/github-only-live-all-v1.json`.

- `GITHUB-ONLY` (including `git hub only`) means fresh GitHub state, Git-only documentation/configuration/test work, and production-apply preparation up to but not including the first live Home Assistant/host mutation.
- Persist deferred rollout state as public-safe `[DEPLOY-QUEUE]` issues in `rozkalnsandris/ops-workflows`; chat or memory is never the queue.
- Because both repositories are public, queue metadata must not expose household/device identifiers, private runtime coordinates/topology, secrets, protected configuration, authenticated storage or sensitive logs.
- Merge remains separately explicit. Neither `GITHUB-ONLY` nor `LIVE-ALL` authorizes merge.
- A GitHub write whose deterministic side effect changes live Home Assistant/host state counts as live work and must not run under `GITHUB-ONLY`.
- Queue `READY` requires the final exact deployable SHA/config target, exact reviewed entrypoint, preflight, verification, allowed mutations/limits and no outstanding separate prerequisite owner gate.
- `LIVE-ALL` snapshots only open `READY` items present at command start, freshly revalidates exact source/target/baseline and may execute only ordinary predeclared production-apply mutations that this repository already permits inside that exact authorization envelope.
- Live `/config` writes, reload/restart/recreate, state-changing Home Assistant services, `.storage`, backups, Cloudflare/ingress, Docker/systemd/host mutation and secrets remain separately gated where the repository-local contract requires it.
- After any selected live mutation starts, error/ambiguity requires public-safe evidence preservation and STOP of the remaining batch; no automatic retry/rollback/cleanup/alternate mutation path unless explicitly pre-authorized.
- Existing public-information and production-safety rules remain authoritative and stricter where applicable.
<!-- END GITHUB-ONLY-LIVE-ALL-V1-MANAGED -->

<!-- BEGIN START-GITHUB-ONLY-V1-MANAGED -->
## START_GITHUB_ONLY_V1 deterministic bootstrap amendment

Startup contract: `rozkalnsandris/ops-workflows/docs/START_GITHUB_ONLY_V1.md`.
Repository manifest: `.github/start-github-only.json`.

- `START <repository> GITHUB-ONLY` refreshes local rules/handoff, the pinned shared policy and START contract, current default branch/governance capability, active PRs, active issues/dependencies, and relevant deploy-queue items before selecting the manifest-defined canonical lane.
- Revalidate mutable GitHub state immediately before every state-dependent write.
- The absence of an open issue alone is NOT a STOP condition. Do not invent speculative work.
- If declared tie-breakers cannot resolve equally authoritative lanes, report `AMBIGUOUS_CANONICAL_LANE` instead of choosing arbitrarily.
- Final routing is one of `READY_FOR_MERGE`, `PARKED`, `STOP_ERROR`, `NEW_SCOPE_OR_RISK`, `AMBIGUOUS_CANONICAL_LANE`, or `IDLE`.
- `PARKED` is session-only. **EXECUTOR** availability is session capability, not **READY** rollout eligibility.
- Executor unavailability alone must not change `READY` to `BLOCKED`; use `BLOCKED` only for rollout eligibility or contract failure.
- Repository-local stricter safety, public-information and trust-boundary rules remain authoritative.
<!-- END START-GITHUB-ONLY-V1-MANAGED -->

<!-- BEGIN AGENT-WORK-CYCLE-V1-MANAGED -->
## Agent Work Cycle v1

Shared governance contract: `rozkalnsandris/ops-workflows/docs/AGENT_WORK_CYCLE_V1.md` with machine invariants in `policy/agent-work-cycle-v1.json`. Repository-local rules remain authoritative and may be stricter.

### Canonical state and minimum-sufficient retrieval

- GitHub is canonical for mutable source, branch, SHA, issue/PR, CI/review and authorization-continuity state. Never reuse mutable state from chat history without a fresh read.
- `START home-assistant-config` uses the repository-local startup routing. Bootstrap only enough state to identify one current work item/lane/gate: current `AGENTS.md`/rules, canonical handoff or continuation when present, current default-branch SHA, and only the issue/PR state required by that lane.
- For a current PR, inspect only the current exact head, required checks, reviews and unresolved threads unless a failure or conflict requires deeper evidence.
- `SYNC home-assistant-config` is incremental refresh of the current lane, not a repo-wide audit. Re-read a handoff only when continuation may have changed or is ambiguous.
- `turpini` resumes the same scope with incremental retrieval. It never creates MERGE, LIVE, retry, rollback, cleanup, credential, permission or runtime authority.
- Do not enumerate unrelated work or historical CI/log/comment/review history during normal START/SYNC. Broaden retrieval only demand-driven or under an explicit repository-local audit mode such as `AUDIT-HANDOFF`.

### Work execution and owner gates

- Prefer the smallest coherent fix and carry safe source/docs/tests/policy work through Draft PR, exact-head CI/review convergence and Ready when repository-local rules permit it.
- Technical intermediate steps such as CI polling, exact-head/diff checks, read-only preflight, evidence refresh and scope-preserving correction are not owner gates.
- MERGE remains an explicit owner decision unless a repository-local explicitly activated FULL mode already grants issue-scoped merge authority. Merge never implies LIVE/deploy authority.
- LIVE/deploy/runtime/credential/permission/production-data mutations require the separate exact authorization defined by repository-local rules.
- Authorization is consumed at the first authorized mutation. After mutation begins, any error, timeout, drift, ambiguity or authorization uncertainty is fail-closed: collect only necessary read-only evidence and STOP. No retry, rollback, cleanup or alternate mutation without fresh explicit authority unless it was pre-authorized.

### Terminal response — exact next command

Every user-visible work-cycle response that ends or pauses repository work must finish with exactly one copy-pasteable command as the final actionable content.

- Use `ACTION REQUIRED` only for a genuine owner authorization/decision gate; never manufacture a gate merely to satisfy this presentation rule.
- When a real owner gate exists, output the exact authorization command with current issue/PR identifiers and exact SHA/target bindings where applicable.
- When no owner gate exists and mutable GitHub/external state must be refreshed, output `SYNC home-assistant-config`.
- When no owner gate exists and same-scope safe technical continuation is immediately available, output `turpini`.
- When the current outcome is complete and no same-scope continuation remains, output `START home-assistant-config`.
- Give exactly one recommended command, not a menu. The response-format contract never grants authority by itself.
<!-- END AGENT-WORK-CYCLE-V1-MANAGED -->
