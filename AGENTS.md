# Repository operating rules

This repository contains private Home Assistant configuration source. Treat every change as potentially production-sensitive even though GitHub Actions are validation-only.

## Source-of-truth boundary

- Track only declarative, reviewable Home Assistant source that has been explicitly classified as safe for Git.
- Never commit secrets, runtime state, recorder/history databases, logs, backups, tokens, private keys, `.storage/`, `.cloud/`, or private media/camera content.
- Do not assume private-repository visibility makes secret storage acceptable.
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
- expose secret values in logs, issues, PRs or CI artifacts.

Prefer supported reload mechanisms for reloadable configuration. A Home Assistant restart is a separate production action when required.

## Live inventory

The first live interaction must be bounded and read-only. Inventory names/types/metadata only as needed to classify files. Never print the contents of `secrets.yaml`, `.storage/`, auth/token material, recorder DBs, private keys, or other excluded runtime data.

## Validation

Before any production apply:

1. repository policy check passes;
2. YAML/static validation passes;
3. exact production Home Assistant version is known;
4. candidate configuration passes Home Assistant `check_config` against that exact intended version;
5. diff is reviewed for secrets/private-runtime leakage;
6. rollback revision and Home Assistant backup path are established;
7. production apply is separately authorized.
