# Deployment contract

## Current status

There is **no automatic deployment** from this repository.

GitHub Actions are validation-only. A merge to `main` must not write to the RPi5, Home Assistant `/config`, Cloudflare, Docker, systemd, integrations, devices or automations.

## Future controlled apply

A production apply may be designed only after the sanitized first import is complete and the live runtime has been inventoried.

Required gates:

1. exact current `main` revision identified;
2. exact production Home Assistant version identified;
3. candidate passes repository policy and static validation;
4. candidate passes Home Assistant `check_config` against the exact intended version;
5. live target files and relevant runtime state are re-read immediately before apply;
6. private Home Assistant backup/rollback evidence exists before mutation;
7. diff is bounded to reviewed files;
8. explicit owner production authorization is obtained;
9. apply preserves file ownership/modes and excludes all runtime-owned paths;
10. supported reload path is preferred where possible;
11. restart/recreate is used only when required and explicitly authorized;
12. post-apply Home Assistant and dashboard health is verified;
13. rollback remains available until verification completes.

## Rollback

Declarative configuration rollback is the previous exact Git revision plus the retained private Home Assistant backup when UI-managed/runtime state could be involved.

Never attempt to restore `.storage`, recorder data or credentials from Git.

## Separation from RPi5_main

This repository will own Home Assistant application configuration only. Host/container/systemd/ingress/backup machinery remains under `RPi5_main` ownership. Any future host-side sync/install helper therefore belongs in `RPi5_main` or must be explicitly delegated by a reviewed cross-repository contract.
