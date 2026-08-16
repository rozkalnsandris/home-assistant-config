# Dependencies

This document will become the reviewed manifest of Home Assistant-side dependencies required by tracked configuration.

## Current state

**Inventory pending.** No HACS/custom-card/custom-integration dependency is assumed yet.

Do not copy the entire generated HACS/community installation into Git by default. Record the dependency and its required version/source here, and track only code/assets that we intentionally own.

## Dependency record format

For each dependency record:

- name;
- type: integration / frontend card / theme / blueprint / other;
- source/project;
- version or exact reviewed revision where practical;
- configuration files that depend on it;
- whether it is installed through HACS, built-in Home Assistant, or manually owned by us;
- recovery/install note;
- last verified Home Assistant version.

## Production version

The exact live Home Assistant version must be inventoried before full configuration CI is enabled. Do not guess or silently use `latest` for acceptance testing.
