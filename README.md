# Home Assistant Config

Public, reviewable source of truth for the safe declarative subset of Home Assistant configuration.

This repository is intentionally separate from `rozkalnsandris/RPi5_main`:

- **this repository** owns only Home Assistant application configuration and dashboard source that has been explicitly reviewed as safe to publish;
- **RPi5_main** owns host/runtime infrastructure, ingress, container/systemd ownership, backups and guarded production operations;
- **Home Assistant backups** remain the recovery source for UI-managed/runtime state that does not belong in Git.

## Current status

Repository safety foundation is merged. Phase 2 adds a **read-only local inventory** tool for the live Home Assistant `/config` directory so we can identify the exact production Home Assistant version, classify top-level config items and locate the YAML source behind the `Mājas YAML` dashboard before importing anything.

No live Home Assistant configuration has been imported or changed yet.

## Public repository safety boundary

Assume every committed byte, commit, PR, issue and Actions log can be read by anyone.

Never commit real `secrets.yaml`, `.storage/`, authentication/session/token material, recorder databases, logs, backups, private keys/certificates, caches, credential-bearing URLs or private media/camera snapshots. Avoid publishing unnecessary private runtime coordinates, device identifiers or household metadata even when they are not credentials.

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

CI validates repository policy, YAML syntax, inventory tooling unit tests and reachable Git history for obvious secret material. After the live Home Assistant version is inventoried, validation will be pinned to that exact version and include Home Assistant's supported configuration check before any production apply.

Home Assistant's container documentation uses:

```text
docker exec homeassistant python -m homeassistant --script check_config --config /config
```

The future CI equivalent must run against an isolated candidate configuration, never against production.

## Documentation

- [Source of truth](docs/SOURCE_OF_TRUTH.md)
- [Dependencies](docs/DEPENDENCIES.md)
- [Deployment contract](docs/DEPLOYMENT.md)
- [Read-only live inventory](docs/LIVE_INVENTORY.md)
- [Repository operating rules](AGENTS.md)
- [Bootstrap roadmap](../../issues/1)
- [Phase 2 inventory issue](../../issues/4)

## Production impact

**Production deploy/change: NO** for repository/inventory tooling changes.
