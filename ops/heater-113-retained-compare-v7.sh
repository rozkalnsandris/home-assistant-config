#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${RETAINED_BASE:?RETAINED_BASE is required}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

container="$(python - <<'PY'
from tools.inventory_home_assistant import running_containers, select_container
print(select_container(running_containers("docker")))
PY
)"

[ -n "$container" ] || {
  printf '{"schema":7,"decision":"DIAGNOSTIC_BLOCKED","reason":"HA_CONTAINER_NOT_FOUND","production_change":false}\n'
  exit 20
}

docker cp "$container:/config/packages/silditajs.yaml" "$tmp/live1" >/dev/null 2>&1 || {
  printf '{"schema":7,"decision":"DIAGNOSTIC_BLOCKED","reason":"LIVE_ORDINAL_1_READ_FAILED","production_change":false}\n'
  exit 20
}
docker cp "$container:/config/packages/heater_scheduler.yaml" "$tmp/live2" >/dev/null 2>&1 || {
  printf '{"schema":7,"decision":"DIAGNOSTIC_BLOCKED","reason":"LIVE_ORDINAL_2_READ_FAILED","production_change":false}\n'
  exit 20
}
docker cp "$container:/config/.storage/scheduler.storage" "$tmp/scheduler" >/dev/null 2>&1 || {
  printf '{"schema":7,"decision":"DIAGNOSTIC_BLOCKED","reason":"SCHEDULER_READ_FAILED","production_change":false}\n'
  exit 20
}

sudo -n python3 - "$RETAINED_BASE" "$tmp/live1" "$tmp/live2" "$tmp/scheduler" <<'PY'
import hmac
import json
import sys
from pathlib import Path

base = Path(sys.argv[1])
live1 = Path(sys.argv[2]).read_bytes()
live2 = Path(sys.argv[3]).read_bytes()
sched = Path(sys.argv[4]).read_bytes()

if not base.is_dir():
    print(json.dumps({
        "schema": 7,
        "decision": "DIAGNOSTIC_BLOCKED",
        "reason": "RETAINED_BASE_MISSING",
        "production_change": False,
    }, indent=2, sort_keys=True))
    raise SystemExit(20)

role_tokens = ("original", "before", "prewrite", "rollback")

def role_signal(p: Path) -> bool:
    rel = "/".join(x.lower() for x in p.relative_to(base).parts)
    return any(token in rel for token in role_tokens)

try:
    current_obj = json.loads(sched.decode())
    current_data = current_obj.get("data") if isinstance(current_obj, dict) else None
    current_schedules = current_data.get("schedules") if isinstance(current_data, dict) else None
    sched_valid = isinstance(current_schedules, list)
except Exception:
    current_schedules = None
    sched_valid = False

m1 = m2 = 0
r1 = r2 = False
semantic_matches = 0
byte_matches = 0
scanned = 0
unreadable = 0

for p in base.rglob("*"):
    try:
        if p.is_symlink() or not p.is_file():
            continue
        if p.stat().st_size > 8 * 1024 * 1024:
            continue
        blob = p.read_bytes()
        scanned += 1
    except Exception:
        unreadable += 1
        continue

    if len(blob) == len(live1) and hmac.compare_digest(blob, live1):
        m1 += 1
        r1 = r1 or role_signal(p)

    if len(blob) == len(live2) and hmac.compare_digest(blob, live2):
        m2 += 1
        r2 = r2 or role_signal(p)

    if sched_valid:
        try:
            obj = json.loads(blob.decode())
            data = obj.get("data") if isinstance(obj, dict) else None
            schedules = data.get("schedules") if isinstance(data, dict) else None
            if isinstance(schedules, list) and schedules == current_schedules:
                semantic_matches += 1
                if len(blob) == len(sched) and hmac.compare_digest(blob, sched):
                    byte_matches += 1
        except Exception:
            pass

if unreadable:
    decision = "DIAGNOSTIC_BLOCKED"
    reason = "PRIVILEGED_RETAINED_READ_INCOMPLETE"
elif m1 > 0 and m2 > 0 and r1 and r2 and sched_valid and len(current_schedules) == 0:
    decision = "FILES_ORIGINAL_SCHEDULER_DRIFTED"
    reason = "HEATER_ORIGINALS_PROVEN_CURRENT_SCHEDULER_EMPTY"
elif m1 > 0 and m2 > 0:
    decision = "DIAGNOSTIC_BLOCKED"
    reason = "RETAINED_MATCH_FOUND_ORIGINAL_ROLE_NOT_PROVEN"
else:
    decision = "FILES_MIXED_OR_OTHER"
    reason = "LIVE_FILES_NOT_MATCHED_TO_COMPLETE_RETAINED_MATERIAL"

print(json.dumps({
    "schema": 7,
    "authorization_112_consumed": True,
    "decision": decision,
    "reason": reason,
    "live_files": {
        "ordinal_1_retained_byte_match_found": m1 > 0,
        "ordinal_2_retained_byte_match_found": m2 > 0,
        "ordinal_1_original_role_signal": r1,
        "ordinal_2_original_role_signal": r2,
    },
    "scheduler": {
        "storage_valid": sched_valid,
        "schedule_count": len(current_schedules) if sched_valid else None,
        "retained_semantic_match_found": semantic_matches > 0,
        "retained_byte_match_found": byte_matches > 0,
    },
    "retained_material": {
        "regular_files_scanned": scanned,
        "unreadable_files": unreadable,
        "privileged_read": True,
    },
    "production_apply_authorized": False,
    "production_mutation": {
        "home_assistant_config_written": False,
        "scheduler_service_called": False,
        "scheduler_storage_written": False,
        "helper_state_changed": False,
        "heater_actuated": False,
        "reload_or_restart": False,
    },
    "privacy": {
        "entity_ids_or_targets_emitted": False,
        "hashes_emitted": False,
        "latent_schedule_values_emitted": False,
        "private_paths_emitted": False,
        "raw_yaml_emitted": False,
        "schedule_days_or_times_emitted": False,
        "secret_aliases_emitted": False,
        "secret_values_emitted": False,
    },
}, indent=2, sort_keys=True))
PY
