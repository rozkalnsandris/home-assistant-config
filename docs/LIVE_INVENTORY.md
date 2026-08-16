# Read-only live inventory

Phase 2 uses a bounded local inventory before any live Home Assistant file is considered for Git.

## What the tool does

`tools/inventory_home_assistant.py`:

1. asks Docker only for running container names/images;
2. auto-selects a single Home Assistant container, or requires `--container NAME` when ambiguous;
3. pipes `tools/ha_inventory_payload.py` to `python -` inside that already-running container;
4. reads `/config` metadata and exact Home Assistant package version;
5. emits top-level names/types plus conservative classification;
6. opens only `configuration.yaml`, and emits from it only bounded Lovelace metadata (`lovelace` presence, YAML mode and safe YAML dashboard filenames);
7. writes the sanitized result to ignored `exports/live-inventory.json` by default.

It does **not** write to `/config`, call Home Assistant services, reload configuration, restart/recreate containers, inspect device state or change production.

## Explicit exclusions

The payload never opens real `secrets.yaml`, `.storage`, `.cloud`, recorder databases/journals, logs, backups, key/certificate files, private media or other known runtime/private entries. It does not traverse excluded runtime directories.

The output intentionally does not contain host mount source paths, internal IP addresses, credential values, tokens or configuration values unrelated to Lovelace dashboard discovery.

## Run

From a checkout of this repository on the Docker host:

```text
python tools/inventory_home_assistant.py
```

If auto-detection finds zero or multiple candidates:

```text
python tools/inventory_home_assistant.py --container <running-container-name>
```

The normal result is written locally to:

```text
exports/live-inventory.json
```

`exports/` is ignored by Git. Do not commit the generated file automatically, even if the scan says it is sanitized.

To inspect the sanitized JSON in the terminal as well:

```text
python tools/inventory_home_assistant.py --stdout
```

## Review gate

Before any information from the generated inventory is copied into a public issue or PR, review it for unnecessary household/device/runtime metadata. Before any actual YAML file is imported, run the repository policy/history checks again and separately inspect the candidate for public-safe content.

## Next step

Use the inventory to identify the exact Home Assistant version and the likely source file behind `Mājas YAML`. The first sanitized configuration/dashboard import is a separate change and still requires review before merge. No inventory run authorizes a production write.
