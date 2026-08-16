# Exact-version Home Assistant validation

The repository records the live Home Assistant release in `home-assistant-version.txt`.

Current reviewed production baseline:

```text
2026.8.2
```

CI never validates against `latest`.

## What CI validates

`tools/build_public_validation_fixture.py` creates a new isolated directory containing only:

- `automations.yaml`;
- `scripts.yaml`;
- `scenes.yaml`;
- a deterministic minimal `configuration.yaml` that includes those three files.

The builder uses a hard-coded allowlist. It does not enumerate the repository and does not copy `secrets.yaml`, `.storage`, dashboards, packages, themes, custom components, databases, logs, backups, media or any other runtime/private content.

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

This gate validates the **published public-safe subset only**. It does not claim that excluded production-private configuration has been validated.

In particular, it does not validate the private production `configuration.yaml`, `Mājas YAML`, private entity/location bindings, heater/cost packages, HACS runtime state, third-party custom integrations, or secrets.

Those items require separate private review and, before production apply, a full candidate configuration check using the exact reviewed production bindings.

## Production boundary

The CI fixture is created only on the GitHub-hosted runner. It has no connection to the live Home Assistant container and cannot reload, restart or mutate production.
