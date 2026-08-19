#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${RETAINED_BASE:?RETAINED_BASE is required}"
: "${LIVE_ONE_PATH:?LIVE_ONE_PATH is required}"
: "${LIVE_TWO_PATH:?LIVE_TWO_PATH is required}"
: "${SCHEDULER_PATH:?SCHEDULER_PATH is required}"

expected_version="2026.8.2"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

emit_blocked() {
  python3 - "$1" <<'PY'
import json,sys
print(json.dumps({
  "schema": 8,
  "decision": "DIAGNOSTIC_BLOCKED",
  "reason": sys.argv[1],
  "production_apply_authorized": False,
  "production_mutation": {
    "home_assistant_config_written": False,
    "scheduler_service_called": False,
    "scheduler_storage_written": False,
    "helper_state_changed": False,
    "heater_actuated": False,
    "reload_or_restart": False,
  },
}, indent=2, sort_keys=True))
PY
  exit 20
}

container="$(python - <<'PY'
from tools.inventory_home_assistant import running_containers, select_container
print(select_container(running_containers("docker")))
PY
)"
[ -n "$container" ] || emit_blocked HA_CONTAINER_NOT_FOUND

inspect_before="$(docker inspect -f '{{.State.Running}}|{{.State.StartedAt}}|{{.RestartCount}}' "$container" 2>/dev/null)" \
  || emit_blocked HA_RUNTIME_INSPECT_FAILED
[ "${inspect_before%%|*}" = "true" ] || emit_blocked HA_NOT_RUNNING

version="$(docker exec "$container" python -m homeassistant --version 2>/dev/null | tr -d '\r\n')" \
  || emit_blocked HA_VERSION_PROBE_FAILED
[ "$version" = "$expected_version" ] || emit_blocked HA_VERSION_MISMATCH

docker cp "$container:$LIVE_ONE_PATH" "$tmp/live1" >/dev/null 2>&1 \
  || emit_blocked LIVE_ORDINAL_1_READ_FAILED
docker cp "$container:$LIVE_TWO_PATH" "$tmp/live2" >/dev/null 2>&1 \
  || emit_blocked LIVE_ORDINAL_2_READ_FAILED
docker cp "$container:$SCHEDULER_PATH" "$tmp/scheduler" >/dev/null 2>&1 \
  || emit_blocked SCHEDULER_READ_FAILED

set +e
sudo -n python3 - "$RETAINED_BASE" "$tmp/live1" "$tmp/live2" "$tmp/scheduler" > "$tmp/worker.json" <<'PY'
import hmac
import json
import sys
from pathlib import Path

base = Path(sys.argv[1])
live1 = Path(sys.argv[2]).read_bytes()
live2 = Path(sys.argv[3]).read_bytes()
current_sched_raw = Path(sys.argv[4]).read_bytes()

required = (
    "candidate-1.bin",
    "candidate-2.bin",
    "original-1.bin",
    "original-2.bin",
    "scheduler-before.bin",
    "rollback-meta.json",
)

if not base.is_dir() or base.is_symlink():
    print(json.dumps({"schema": 8, "decision": "DIAGNOSTIC_BLOCKED", "reason": "RETAINED_BASE_INVALID"}, indent=2, sort_keys=True))
    raise SystemExit(20)

eligible = []
for d in [base, *base.rglob("*")]:
    try:
        if d.is_symlink() or not d.is_dir():
            continue
        ok = True
        for name in required:
            p = d / name
            if p.is_symlink() or not p.is_file():
                ok = False
                break
        if ok:
            eligible.append(d)
    except Exception:
        continue

if len(eligible) != 1:
    print(json.dumps({
        "schema": 8,
        "decision": "DIAGNOSTIC_BLOCKED",
        "reason": "EXACT_RETAINED_ATTEMPT_CARDINALITY_INVALID",
        "retained_attempt_count": len(eligible),
    }, indent=2, sort_keys=True))
    raise SystemExit(20)

d = eligible[0]
try:
    candidate1 = (d / "candidate-1.bin").read_bytes()
    candidate2 = (d / "candidate-2.bin").read_bytes()
    original1 = (d / "original-1.bin").read_bytes()
    original2 = (d / "original-2.bin").read_bytes()
    before_raw = (d / "scheduler-before.bin").read_bytes()
    meta = json.loads((d / "rollback-meta.json").read_text(encoding="utf-8"))
except Exception:
    print(json.dumps({"schema": 8, "decision": "DIAGNOSTIC_BLOCKED", "reason": "EXACT_RETAINED_READ_FAILED"}, indent=2, sort_keys=True))
    raise SystemExit(20)

meta_valid = isinstance(meta, dict)

def eq(a: bytes, b: bytes) -> bool:
    return len(a) == len(b) and hmac.compare_digest(a, b)

try:
    current_obj = json.loads(current_sched_raw.decode())
    before_obj = json.loads(before_raw.decode())
    current_data = current_obj.get("data") if isinstance(current_obj, dict) else None
    before_data = before_obj.get("data") if isinstance(before_obj, dict) else None
    current_schedules = current_data.get("schedules") if isinstance(current_data, dict) else None
    before_schedules = before_data.get("schedules") if isinstance(before_data, dict) else None
    current_valid = isinstance(current_schedules, list)
    before_valid = isinstance(before_schedules, list)
except Exception:
    current_obj = before_obj = None
    current_schedules = before_schedules = None
    current_valid = before_valid = False

result = {
    "schema": 8,
    "authorization_112_consumed": True,
    "retained": {
        "exact_attempt_count": 1,
        "rollback_meta_valid": meta_valid,
    },
    "live_files": {
        "ordinal_1_equals_original": eq(live1, original1),
        "ordinal_1_equals_candidate": eq(live1, candidate1),
        "ordinal_2_equals_original": eq(live2, original2),
        "ordinal_2_equals_candidate": eq(live2, candidate2),
    },
    "scheduler": {
        "current_storage_valid": current_valid,
        "before_storage_valid": before_valid,
        "current_schedule_count": len(current_schedules) if current_valid else None,
        "before_schedule_count": len(before_schedules) if before_valid else None,
        "bytes_equal_prewrite_snapshot": eq(current_sched_raw, before_raw),
        "parsed_json_equal_prewrite_snapshot": current_obj == before_obj if current_valid and before_valid else False,
        "schedules_equal_prewrite_snapshot": current_schedules == before_schedules if current_valid and before_valid else False,
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
}

files_original = (
    result["live_files"]["ordinal_1_equals_original"]
    and not result["live_files"]["ordinal_1_equals_candidate"]
    and result["live_files"]["ordinal_2_equals_original"]
    and not result["live_files"]["ordinal_2_equals_candidate"]
)
scheduler_empty = current_valid and before_valid and len(current_schedules) == 0 and len(before_schedules) == 0

if files_original and scheduler_empty and result["scheduler"]["bytes_equal_prewrite_snapshot"]:
    result["decision"] = "FILES_ORIGINAL_SCHEDULER_RESTORED_EXACT"
    result["reason"] = "HEATER_ORIGINALS_AND_EXACT_PREWRITE_SCHEDULER_PROVEN"
elif files_original and scheduler_empty and result["scheduler"]["schedules_equal_prewrite_snapshot"]:
    result["decision"] = "FILES_ORIGINAL_SCHEDULER_SEMANTICALLY_RESTORED"
    result["reason"] = "HEATER_ORIGINALS_PROVEN_SCHEDULER_EMPTY_SEMANTICS_EQUAL"
else:
    result["decision"] = "FILES_MIXED_OR_OTHER"
    result["reason"] = "EXACT_RETAINED_COMPARISON_DID_NOT_PROVE_FULL_RESTORE"

print(json.dumps(result, indent=2, sort_keys=True))
PY
worker_rc=$?
set -e

[ -s "$tmp/worker.json" ] || emit_blocked EXACT_RETAINED_WORKER_EMPTY
python3 -m json.tool "$tmp/worker.json" >/dev/null 2>&1 || emit_blocked EXACT_RETAINED_WORKER_INVALID_JSON

if ! docker exec "$container" python -m homeassistant --script check_config -c /config >/dev/null 2>&1; then
  emit_blocked LIVE_CHECK_CONFIG_FAILED
fi

inspect_after="$(docker inspect -f '{{.State.Running}}|{{.State.StartedAt}}|{{.RestartCount}}' "$container" 2>/dev/null)" \
  || emit_blocked HA_RUNTIME_REINSPECT_FAILED
[ "$inspect_after" = "$inspect_before" ] || emit_blocked HA_RUNTIME_CHANGED_DURING_DIAGNOSTIC

python3 - "$tmp/worker.json" "$version" <<'PY'
import json,sys
p=sys.argv[1]
obj=json.load(open(p,encoding="utf-8"))
obj["home_assistant"]={
    "version":sys.argv[2],
    "running":True,
    "live_check_config_passed":True,
    "runtime_unchanged_during_snapshot":True,
}
print(json.dumps(obj,indent=2,sort_keys=True))
PY

exit "$worker_rc"
