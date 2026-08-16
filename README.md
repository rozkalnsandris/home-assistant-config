# Home Assistant Config

Public, reviewable source of truth for the safe declarative subset of Home Assistant configuration.

This repository is intentionally separate from `rozkalnsandris/RPi5_main`:

- **this repository** owns only Home Assistant application configuration and dashboard source that has been explicitly reviewed as safe to publish;
- **RPi5_main** owns host/runtime infrastructure, ingress, container/systemd ownership, backups and guarded production operations;
- **Home Assistant backups** remain the recovery source for UI-managed/runtime state that does not belong in Git.

## Current status

Repository safety foundation only. No live Home Assistant configuration has been imported or changed yet.

The first target after this foundation is a **read-only inventory** of the live Home Assistant `/config` directory, including identification of the YAML source behind the `Mājas YAML` dashboard. Only after every top-level item is classified as `TRACK`, `IGNORE`, `BACKUP_ONLY` or `REVIEW` will a sanitized configuration snapshot be proposed.

## Public repository safety boundary

Assume every committed byte, commit, PR, issue and Actions log can be read by anyone.

Never commit real `secrets.yaml`, `.storage/`, authentication/session/token material, recorder databases, logs, backups, private keys/certificates, caches, credential-bearing URLs or private media/camera snapshots. Avoid publishing unnecessary private runtime coordinates, device identifiers or household metadata even when they are not credentials.

A file is not safe merely because Home Assistant accepts it. Public-safe review is a separate gate before import.

## Workflow

`issue → fresh branch → focused changes → Draft PR → CI → review → Ready → explicit owner squash merge → production-change classification`

A merge never deploys automatically. GitHub Actions in this repository are validation-only and must not write to the live Home Assistant instance.

## Validation model

Phase 1 validates repository policy, YAML syntax and reachable Git history for obvious secret material. After the live Home Assistant version is inventoried, validation will be pinned to that exact version and include Home Assistant's supported configuration check before any production apply.

Home Assistant's container documentation uses:

```text
docker exec homeassistant python -m homeassistant --script check_config --config /config
```

The future CI equivalent must run against an isolated candidate configuration, never against production.

## Documentation

- [Source of truth](docs/SOURCE_OF_TRUTH.md)
- [Dependencies](docs/DEPENDENCIES.md)
- [Deployment contract](docs/DEPLOYMENT.md)
- [Repository operating rules](AGENTS.md)
- [Bootstrap roadmap](../../issues/1)

## Production impact

**Production deploy/change: NO** for the repository bootstrap.
