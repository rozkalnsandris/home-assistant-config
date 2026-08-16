# Source of truth

## Purpose

This repository is the source of truth for **reviewable declarative Home Assistant configuration** only.

It is not a byte-for-byte mirror of the live `/config` directory and is not a replacement for Home Assistant backups.

## Ownership split

| Layer | Source of truth |
|---|---|
| Home Assistant declarative YAML/dashboard source | `home-assistant-config` |
| Host/container/systemd/ingress/backup ownership | `RPi5_main` |
| UI-managed Home Assistant runtime state | live Home Assistant + HA backups |
| Secrets/credentials | private runtime secret storage, never Git |
| Recorder/history data | live database + HA backups, never Git |

## Classification model

Every live top-level item must be classified before import:

- `TRACK` — declarative, reviewable and safe for Git;
- `IGNORE` — generated/runtime/local content that Git should never own;
- `BACKUP_ONLY` — required for recovery but inappropriate for source control;
- `REVIEW` — ownership/sensitivity is not yet clear and requires explicit inspection.

Nothing enters Git by default merely because it exists under `/config`.

## Expected TRACK candidates

Subject to live inventory and review:

- `configuration.yaml`;
- explicit YAML dashboards and includes;
- YAML-managed automations/scripts/scenes where appropriate;
- packages, templates and themes;
- authored blueprints;
- authored `www/` assets;
- self-authored custom integrations/cards;
- safe dependency manifests/documentation;
- `secrets.yaml.example` containing names/placeholders only.

## Always excluded

- real `secrets.yaml`;
- `.storage/` and `.cloud/`;
- auth/session/token data;
- private keys/certificates;
- recorder/history databases and journals;
- logs;
- backups/archives;
- caches/temp/runtime-generated state;
- private media/camera snapshots.

## Dashboard policy

The existing `Mājas YAML` dashboard is the first source-control target. Preserve YAML ownership. If an important dashboard turns out to be storage-managed, do not commit `.storage` data; instead review whether it should be migrated/exported into an explicit YAML dashboard through a separate change.

## Recovery

Use both layers:

1. Git for review, history, rollback and reproducible declarative configuration;
2. Home Assistant backups for complete-instance recovery, including UI-managed/runtime state that Git intentionally excludes.
