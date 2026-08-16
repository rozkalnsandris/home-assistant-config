# Mājas YAML dashboard refactor

Issue #26 is the roadmap for replacing the current monolithic `Mājas YAML` dashboard with a maintainable split YAML structure.

Official Home Assistant references used for this design:

- configuration splitting and nested `!include*`: https://www.home-assistant.io/docs/configuration/splitting_configuration/
- YAML dashboards: https://www.home-assistant.io/dashboards/dashboards/
- Sections views: https://www.home-assistant.io/dashboards/sections/
- configuration validation: https://www.home-assistant.io/docs/tools/check_config/

## Target shape

The intended hierarchy is:

`dashboard → view → section → cards`

The private dashboard tree should keep one ordered file per view and one ordered file per meaningful section. Numeric prefixes such as `00_`, `10_`, `20_` keep `!include_dir_list` ordering obvious and stable.

Do not split down to one file per tiny card. That would replace one monolith with a different maintenance problem.

Preferred include rules:

- `!include_dir_list` for one view or one section object per file;
- `!include` for one explicit block;
- `!include_dir_merge_list` only when every included file is itself a YAML list.

## Migration order

1. **Read-only inventory.** Quantify the current dashboard without publishing its private contents.
2. **1:1 split.** Move existing behavior into ordered view/section files without redesigning the UI.
3. **Sections modernization.** Convert suitable views to native Sections/grid layout and reduce unnecessary stack nesting.
4. **Content cleanup.** Deduplicate layout and remove obsolete cards only with evidence that they are unused.
5. **Controlled cutover.** Backup, exact-version validation, separately authorized production apply, and LAN/remote verification.

The split phase and the UI modernization phase are intentionally separate so regressions remain attributable.

## Private-safe structural audit

Issue #27 provides the first gate:

```bash
sudo python -B tools/audit_majas_dashboard_structure.py --stdout
```

Run it only from the exact reviewed repository revision. The host-side tool passes a bounded probe into the running Home Assistant container. Private YAML is parsed inside the container and is never returned to the host tool's stdout. The public report contains structural counts and anonymous `view_00`, `view_01`, ... complexity summaries only.

The report must not emit:

- exact dashboard paths;
- raw YAML;
- entity IDs;
- view titles/paths/icons;
- card titles;
- custom-card type names;
- resolved secret values.

A successful inventory returns `READY_FOR_SPLIT_DESIGN`. `NEEDS_REVIEW` means the dashboard was found but its current shape needs a narrower classifier before migration. `BLOCKED` means version/binding/privacy evidence is not safe enough to proceed.

## Production boundary

This roadmap, its audit tooling and source-only split PRs do not authorize any live dashboard write, reload or Home Assistant restart. Production cutover requires a fresh backup/rollback gate, exact-version validation and explicit owner authorization.
