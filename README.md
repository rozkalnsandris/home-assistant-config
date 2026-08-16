# Home Assistant Config

Public, reviewable source of truth for the safe declarative subset of Home Assistant configuration.

This repository is intentionally separate from `rozkalnsandris/RPi5_main`:

- **this repository** owns only Home Assistant application configuration and dashboard source that has been explicitly reviewed as safe to publish;
- **RPi5_main** owns host/runtime infrastructure, ingress, container/systemd ownership, backups and guarded production operations;
- **Home Assistant backups** remain the recovery source for UI-managed/runtime state that does not belong in Git.

## Current status

Repository safety foundation, read-only live inventory and exact-version validation are complete. The reviewed production baseline is Home Assistant `2026.8.2`.

Public Git now contains the audited generic automations/scripts/scenes plus the sanitized core configuration/package structure. Complex household-specific mappings are referenced through local-only `private/*.yaml`; scalar private package values use Home Assistant `!secret` bindings. Neutral examples document the expected local shape without publishing the real values.

The real YAML dashboard source, real reverse-proxy trust list, real household entity bindings, secrets, unresolved theme provenance, runtime state and generated third-party code remain outside public Git.

## Public repository safety boundary

Assume every committed byte, commit, PR, issue and Actions log can be read by anyone.

Never commit real `secrets.yaml`, `private/`, `.storage/`, authentication/session/token material, recorder databases, logs, backups, private keys/certificates, caches, credential-bearing URLs or private media/camera snapshots. Avoid publishing unnecessary private runtime coordinates, device identifiers or household metadata even when they are not credentials.

A file is not safe merely because Home Assistant accepts it. Public-safe review is a separate gate before import.

## Read-only live inventory

The inventory helper is intentionally local-only and writes to ignored `exports/` by default:

```text
python tools/inventory_home_assistant.py
```

It runs read-only against the already-running Home Assistant Docker container and emits only bounded metadata. It never opens real secrets/runtime files and never writes to `/config`.

See [Read-only live inventory](docs/LIVE_INVENTORY.md) for the exact safety contract and fallback `--container` usage.

## Workflow

`issue → fresh branch → focused changes → Draft PR → CI → review → Ready → explicit owner squash merge → production-change classification`

A merge never deploys automatically. GitHub Actions in this repository are validation-only and must not write to the live Home Assistant instance.

## Validation model

The production Home Assistant release is pinned in `home-assistant-version.txt`.

CI creates a fresh fixture from a hard allowlist of public source, injects neutral dummy private bindings and typed dummy secret values, and runs Home Assistant's official `check_config --fail-on-warnings` against the exact pinned release. CI never reads production-private files and never validates against `latest`.

This is intentionally **public-structure validation**, not proof that the real private bindings are correct. A complete production candidate still requires a separate local private-binding validation gate before apply.

See [Exact-version validation](docs/VALIDATION.md) and [Private binding contract](docs/PRIVATE_BINDINGS.md).

## Documentation

- [Source of truth](docs/SOURCE_OF_TRUTH.md)
- [Dependencies](docs/DEPENDENCIES.md)
- [Deployment contract](docs/DEPLOYMENT.md)
- [Read-only live inventory](docs/LIVE_INVENTORY.md)
- [Exact-version validation](docs/VALIDATION.md)
- [Private binding contract](docs/PRIVATE_BINDINGS.md)
- [Repository operating rules](AGENTS.md)
- [Bootstrap roadmap](../../issues/1)

## Production impact

**Production deploy/change: NO** for repository, audit and validation-only changes.
