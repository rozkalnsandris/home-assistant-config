# Private binding contract

This repository is public. Public YAML owns reusable Home Assistant structure and logic; household-specific bindings remain local.

## Native Home Assistant split

`configuration.yaml` uses Home Assistant's native `!include` support for complex private blocks under `private/`. Scalar package bindings use `!secret` and resolve from the local `secrets.yaml`.

Real files are intentionally absent from Git:

- `private/customize.yaml`
- `private/http.yaml`
- `private/lovelace.yaml`
- `private/utility_meter.yaml`
- `private/recorder.yaml`
- `secrets.yaml`

Public `private.example/` files describe only the expected shape with neutral placeholders. They are not production values and must never be copied blindly.

## Why the split exists

Complex blocks such as `customize`, reverse-proxy trust, dashboard registration, recorder exclusions and utility-meter source entity IDs contain household/runtime topology. Keeping those entire mappings local avoids trying to force private mapping keys or nested structures into scalar secret substitutions.

Package values that are naturally scalar use `!secret` instead:

- heater switch entity ID;
- heater schedule initial time;
- electricity price per kWh;
- monthly fixed electricity cost.

Home Assistant resolves secrets from `secrets.yaml`; storing a value there separates it from public YAML but does not encrypt it. Keep local secrets protected and covered by the normal Home Assistant backup process.

## Validation

CI never reads local `private/` or a real `secrets.yaml`. The fixture builder copies a hard allowlist of public files, creates neutral dummy private bindings and dummy typed secret values in a fresh temporary directory, then runs the exact pinned Home Assistant release with `check_config --fail-on-warnings`.

A green public CI run proves the public structure is compatible with the pinned Home Assistant release. It does not prove the real private bindings are correct. Full production-candidate validation remains a separate local gate before any apply.

## Production rule

Git merge does not copy, rewrite, reload or restart the live Home Assistant instance. Production changes require a separate exact-revision, backup, local-private-binding and validation gate.
