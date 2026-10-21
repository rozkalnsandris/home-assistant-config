# GitHub-first / bounded RDC fallback v1 — home-assistant-config

Source-only adoption for issue #164 of `rozkalnsandris/ops-workflows@4e89b75d7f496dfb43fd9c1eb10a995fad53fdd8` (normative `docs/GITHUB_FIRST_RDC_FALLBACK_V1.md`, machine contract `policy/github-first-rdc-fallback-v1.json`). Local machine adapter: `.github/github-first-rdc-v1.json`.

## Decision before each step

1. Use connected GitHub actions for repository content, source, PR/issue, commits/branches, CI, reviews and continuity wherever the available action gives equivalent correctness, authorization and durable evidence.
2. Prefer GitHub-hosted CI for safe source validation where equivalent. No RDC `git`, `gh`, `curl`, checkout or SSH in place of GitHub-native operations.
3. A genuine host-local Home Assistant/systemd/container/config observation may use the **minimum** RDC step only when GitHub plus hosted CI cannot substitute and existing exact read/LIVE authority covers the target. Record a sanitized exception beforehand. Return to GitHub after that step.
4. A GitHub error, 403/429, rate limit, timeout, uncertain write, absent authority, or an unproven capability gap is a STOP—not permission to retry, roll back or use RDC to write GitHub.

Required exception fields: `STEP`, `REPOSITORY_AND_TARGET`, `GITHUB_CAPABILITY_CHECKED`, `PRECISE_CAPABILITY_GAP`, `GITHUB_CI_NOT_EQUIVALENT_REASON`, `MINIMAL_RDC_OPERATION`, `EXISTING_AUTHORIZATION`, `GITHUB_RETURN_EVIDENCE`. This is a **public repository**: do not publish private HA configuration, `.storage`, entity/device/household identifiers, credentials, URLs, tokens, private runtime coordinates or raw logs.

## Preserved behavior and owner gates

- Bare `START home-assistant-config` and `turpini` remain FAST-LANE v2.2. `AUTO-RUN FULL home-assistant-config #<issue>`, `GITHUB-ONLY`, `LIVE-ALL` remain separate explicitly requested modes; this change must not activate any of them.
- Source-only safe work may proceed to Ready under existing repository workflow. **MERGE remains owner-gated** outside a separately valid exact issue-scoped FULL activation. A merge never applies Home Assistant production changes.
- Live `/config` changes, reload/restart/recreate, device/helper/automation actuation, storage, backups, Cloudflare/ingress, Docker/systemd/host changes, secrets, permissions and private material retain the existing exact separate LIVE/trust boundary.
- The controller issue #140, other issues/PRs, YAML/device configuration, infrastructure source, deployment routes, `.github/start-mode-routing.json` and existing GitHub access policy are unchanged.
- This policy adds no autonomous deployment, automatic retries, GitHub Settings mutation or hidden host access. Existing stricter `AGENTS.md` rules win.

## Acceptance

Four source-only files: `AGENTS.md`, `.github/github-first-rdc-v1.json`, `docs/GITHUB_FIRST_RDC_ADOPTION_V1.md`, `tests/test_github_first_rdc_v1.py`. Existing GitHub-hosted `validate` workflow executes new tests via `python -m unittest discover -s tests -v`; successful CI is source proof only, not Home Assistant LIVE evidence. Explicit owner merge approval required.
