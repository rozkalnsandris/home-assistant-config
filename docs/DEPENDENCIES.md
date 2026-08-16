# Dependencies

This document records Home Assistant-side dependencies required by configuration that may eventually be tracked here.

## Production baseline

- Home Assistant: `2026.8.2`
- Dependency evidence source: private, read-only inventory/audit performed before publication.
- Generated HACS/community bundles and third-party custom integrations are **documented, not vendored**.

## Custom integrations

| Name | Installed version | Source | Policy | Notes |
| --- | --- | --- | --- | --- |
| Browser Mod | `3.2.1` | `thomasloven/hass-browser_mod` | DOCUMENT_ONLY | Used by the YAML dashboard. |
| HACS | `2.0.5` | `hacs/integration` | DOCUMENT_ONLY | Dependency manager/runtime integration; never vendor generated state. |
| Scheduler | manifest reports `v0.0.0` | `nielsfaber/scheduler-component` | DOCUMENT_ONLY | Used by heater scheduling logic; manifest version is not treated as a reliable release pin. |
| Hermes Agent (`hermes_conversation`) | `1.1.0` | `WolframRavenwolf/hermes-ha-integration` | DOCUMENT_ONLY | Every privately reviewed core source file exactly matched upstream tag `v1.1.0` by Git blob SHA. Upstream is MIT licensed. |
| Assist TTS Router (`assist_tts_router`) | `0.2.0` | `xiasi0/assist_tts_router` | DOCUMENT_ONLY | Every privately reviewed source file exactly matched the reviewed public upstream files by Git blob SHA. The upstream repository did not expose license metadata during the audit, so source is not vendored here. |

## Frontend / Lovelace dependencies

The private audit confirmed installed community directories matching the custom cards used by the YAML dashboard:

- Bubble Card;
- ApexCharts Card;
- Better Thermostat UI Card;
- Button Card;
- Auto Entities;
- Card Mod;
- Mushroom;
- State Switch;
- Stack In Card;
- Scheduler Card.

The audit also observed other community bundles. They may belong to the storage-managed dashboard or historical configuration, so they are not declared unused solely from this evidence.

Exact frontend package versions were not recovered from private HACS storage because `.storage/` is intentionally excluded from public-source inventory. When a precise version becomes necessary for recovery or validation, record it from a safe upstream/HACS metadata source rather than copying `.storage` into Git.

## Source policy

Do not copy the entire generated HACS/community installation into this repository. Record dependency name, source and version/revision when safely known. Track third-party code only when there is an explicit reason, compatible licensing, and a separate review.

Self-authored configuration or assets may be tracked only after public-information and provenance review.
