# Exact-version Home Assistant validation

The repository records the live Home Assistant release in `home-assistant-version.txt`.

Current reviewed production baseline:

```text
2026.8.2
```

CI never validates against `latest`.

## What CI validates

`tools/build_public_validation_fixture.py` creates a fresh isolated candidate from a hard allowlist of tracked public source:

- `configuration.yaml`;
- `automations.yaml`;
- `scripts.yaml`;
- `scenes.yaml`;
- the three audited public package files under `packages/`.

The public `configuration.yaml` intentionally refers to local-only `private/*.yaml` blocks, and the packages intentionally refer to scalar `!secret` bindings. CI does **not** read those values from the repository or from production. Instead, the fixture builder creates neutral dummy `private/*.yaml`, a dummy typed `secrets.yaml`, and a dummy theme file inside the temporary runner directory.

The builder does not enumerate or copy a real `private/`, real `secrets.yaml`, `.storage`, dashboards, custom components, databases, logs, backups, media or other runtime/private content.

GitHub Actions then pulls:

```text
ghcr.io/home-assistant/home-assistant:2026.8.2
```

and runs Home Assistant's supported configuration checker with warnings treated as failures:

```text
python -m homeassistant --script check_config --config /config --fail-on-warnings
```

The resolved container image digest is printed into the Actions log as execution evidence.

## What this does not prove

This gate validates the **published public structure plus neutral CI bindings**. It does not claim that the real production-private binding values are correct.

In particular, it does not validate the real reverse-proxy trust list, household entity IDs, the real YAML dashboard source, recorder/utility-meter private bindings, HACS runtime state, third-party custom integrations, or real secret values.

Those items require a separate private full-candidate check before production apply.

## Production boundary

The CI fixture exists only on the GitHub-hosted runner. It has no connection to the live Home Assistant container and cannot reload, restart or mutate production.
