# AUTO-RUN FULL v2 — Home Assistant issue-to-DONE orchestration

Canonical machine contract: `.github/auto-run-full-v2.json`
Controller: `home-assistant-config#140`
Adoption issue: `home-assistant-config#141`

## Operating model

`AUTO-RUN FULL` is the normal issue-scoped implementation lane. `FAST-LANE v2.2` remains the safe discovery, audit and non-FULL continuation lane.

The owner activates FULL only with the exact command:

```text
AUTO-RUN FULL home-assistant-config #<issue>
```

That command freezes one authorization envelope for the exact open implementation issue. It is never inferred from `START`, `turpini`, controller state, handoff continuity, chat history, a prior receipt or deploy-queue state.

For the frozen issue, the source flow is:

```text
source -> Draft PR -> CI -> review/corrections -> final exact-head gate -> squash merge -> post-merge verification -> production-change classification
```

Routine source analysis, edits, tests, branch/commit/push, Draft PR creation/update, CI inspection, focused corrections, review/comment ingestion and ordinary merge-conflict correction are technical steps rather than new owner gates.

## Merge authority

A valid current `AUTO-RUN FULL home-assistant-config #<issue>` command is one explicit owner authorization to squash-merge only the canonical PR implementing that frozen issue, after the final exact-head readiness gate.

Before merge, all of the following must be freshly true for the exact current PR head:

- the target issue and activation receipt still match;
- the PR is the canonical implementation for the frozen issue;
- the exact head SHA is freshly read;
- final diff, scope, privacy and public-information review is complete;
- required CI is green;
- unresolved actionable review findings are zero;
- mergeability is acceptable;
- repository protections/rules, if any, are satisfied;
- no force/history rewrite or scope expansion is needed.

If the PR head changes, previous merge readiness is void and the new head must be freshly revalidated.

GitHub native auto-merge is preferred when repository capability is available. If it is unavailable, a direct exact-head **squash** merge is allowed only inside the same frozen FULL merge authority and only after the same readiness gate.

Outside a valid FULL activation, merge retains the ordinary explicit owner squash-merge gate defined by `AGENTS.md`.

## Home Assistant production boundary

AUTO-RUN FULL in this repository never grants Home Assistant production/live authority.

After merge, classify exactly one:

```text
Production deploy/change REQUIRED: YES
Production deploy/change REQUIRED: NO
```

If `NO`, and the issue Definition of Done is otherwise proven, the run may complete `DONE` after exact post-merge `main` verification and the final public-safe receipt.

If `YES`, the controller enters `PAUSED_PRODUCTION_AUTH` unless the production portion of the issue is intentionally out of scope. A separate precise current owner authorization is required before any production mutation. It must bind the exact source/config revision, exact target/action, expected baseline, allowed mutation class, practical limits, explicit exclusions and recovery semantics.

The FULL command never by itself authorizes:

- live Home Assistant `/config` writes;
- reload, restart or container recreate;
- Scheduler or other UI-managed configuration mutation;
- helper, entity or device state mutation or actuation;
- `.storage` changes;
- backup create/delete/restore;
- secret/credential access or mutation;
- host/runtime/infrastructure mutation owned by `RPi5_main`;
- arbitrary SSH/shell authority, database writes or new trust-boundary changes.

The first separately authorized production mutation consumes that one-shot authorization. If an error, ambiguity or unexpected drift appears after mutation begins, preserve public-safe evidence and `STOP_ERROR`; do not improvise retry, rollback, cleanup or an alternate mutation path unless those semantics were explicitly pre-authorized.

Merge authorization is never production authorization.

## Activation transaction

Before activation, freshly read:

1. `AGENTS.md`;
2. `.github/start-mode-routing.json`;
3. `.github/auto-run-full-v2.json`;
4. the exact target issue and Definition of Done;
5. current `main`;
6. active PR, CI, review and relevant comment state;
7. relevant handoff/dependencies;
8. controller issue `#140`.

Activation fails closed if another implementation issue is already active.

Materialize an owner-identity activation receipt on the target issue using schema:

```text
rozkalns.auto-run-full-authorization.v2
```

The receipt freezes repository, issue number, issue Definition of Done, allowed source actions, merge authority, retry/rollback semantics and explicit exclusions. It must explicitly state that production authority is not included. Later issue edits may reduce or clarify scope but never silently expand authority.

Because this repository is public, controller and receipt content must not expose household-private YAML, exact household entity/device/config-entry identifiers, secret aliases or values, private paths/targets, schedule values, private URLs, camera/media/presence data or other unnecessary private runtime information.

## Source convergence loop

Each worker run makes the maximum coherent progress supported by fresh GitHub evidence:

1. refresh rules, issue/controller, handoff/current `main`, canonical PR and CI/review/comments;
2. select the next action inside frozen scope;
3. implement the smallest coherent source change;
4. validate and inspect exact diff for scope, secrets and public-information leakage;
5. commit exact paths and push;
6. create/update the canonical Draft PR;
7. inspect required CI and review state;
8. correct branch-caused failures or actionable findings without scope expansion;
9. repeat until exact-head source/review/CI convergence;
10. perform the final exact-head merge gate;
11. squash-merge the canonical PR under frozen FULL authority;
12. verify exact post-merge `main` and Definition of Done;
13. classify production change required YES/NO;
14. if production authority is separately required, persist `PAUSED_PRODUCTION_AUTH` and STOP at that gate;
15. otherwise write the final receipt and return controller `#140` to `IDLE`.

Three materially identical failed attempts without a materially new safe hypothesis produce `STOP_ERROR`.

## Controller states

```text
IDLE
ACTIVATING
WORKING
WAITING_CI
WAITING_REVIEW
CORRECTING
WAITING_EVENT_RESUME
WAITING_WATCHDOG_RESUME
PAUSED_USAGE
PAUSED_PLATFORM_APPROVAL
PAUSED_EXTERNAL
PAUSED_PRODUCTION_AUTH
VERIFYING
DONE
STOP_SCOPE_OR_RISK
STOP_ERROR
```

A chat turn ending, CI wait or review wait is resumable state, not a new owner gate. GitHub remains canonical continuity.

## Resume architecture

When configured, GitHub event-triggered ChatGPT Work is the preferred low-latency resume path for supported PR activity. An hourly ChatGPT Scheduled Task may be used as a durable watchdog/fallback. Neither path creates authority; each must reconstruct the frozen active issue from GitHub.

If those product-level resume mechanisms are not configured or temporarily unavailable, manual `turpini` may resume only an already-active frozen FULL issue; it does not activate FULL or create merge/production authority.

## Billing boundary

- no `OPENAI_API_KEY`;
- no provider LLM API keys;
- no token-billed fallback;
- no automatic paid-credit purchase;
- Codex and Copilot are optional, not correctness dependencies.

If product usage is exhausted, persist `PAUSED_USAGE`; do not silently change billing mode.

## Terminal behavior

Normal terminal state is `DONE` only when the target Definition of Done is proven, exact post-merge `main` is verified, production-change classification is recorded, any separately authorized production work required by the issue has also been verified, a final public-safe GitHub receipt is written and controller `#140` returns to `IDLE`.

Notify the owner for `DONE`, `PAUSED_PRODUCTION_AUTH`, `STOP_SCOPE_OR_RISK`, `STOP_ERROR` or a platform-level approval that requires owner action. Routine source implementation progress does not require owner nudges.
