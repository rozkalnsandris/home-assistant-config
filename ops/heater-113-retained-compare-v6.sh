#!/usr/bin/env bash
set -euo pipefail
umask 077
retained_base="$HOME/.local/share/ha-private-rollbacks/heater-112"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
container="$(python - <<'PY'
from tools.inventory_home_assistant import running_containers, select_container
print(select_container(running_containers("docker")))
PY
)"
[ -n "$container" ] || exit 20
docker cp "$container:/config/packages/silditajs.yaml" "$tmp/live1" >/dev/null 2>&1
docker cp "$container:/config/packages/heater_scheduler.yaml" "$tmp/live2" >/dev/null 2>&1
docker cp "$container:/config/.storage/scheduler.storage" "$tmp/scheduler" >/dev/null 2>&1
python - "$retained_base" "$tmp/live1" "$tmp/live2" "$tmp/scheduler" <<'PY'
import hmac,json,sys
from pathlib import Path
base=Path(sys.argv[1]); live1=Path(sys.argv[2]).read_bytes(); live2=Path(sys.argv[3]).read_bytes(); sched=Path(sys.argv[4]).read_bytes()
if not base.is_dir():
    print(json.dumps({"schema":6,"decision":"DIAGNOSTIC_BLOCKED","reason":"RETAINED_BASE_MISSING","production_change":False},indent=2)); raise SystemExit(20)
role_tokens=("original","before","prewrite","rollback")
def role(p):
    s="/".join(x.lower() for x in p.relative_to(base).parts)
    return any(t in s for t in role_tokens)
try:
    obj=json.loads(sched.decode()); data=obj.get("data") if isinstance(obj,dict) else None; schedules=data.get("schedules") if isinstance(data,dict) else None
    sched_valid=isinstance(schedules,list)
except Exception:
    schedules=None; sched_valid=False
m1=m2=0; r1=r2=False; sem=byte=0; scanned=unreadable=0
for p in base.rglob("*"):
    try:
        if p.is_symlink() or not p.is_file() or p.stat().st_size>8*1024*1024: continue
        b=p.read_bytes(); scanned+=1
    except Exception:
        unreadable+=1; continue
    if len(b)==len(live1) and hmac.compare_digest(b,live1): m1+=1; r1=r1 or role(p)
    if len(b)==len(live2) and hmac.compare_digest(b,live2): m2+=1; r2=r2 or role(p)
    if sched_valid:
        try:
            o=json.loads(b.decode()); d=o.get("data") if isinstance(o,dict) else None; s=d.get("schedules") if isinstance(d,dict) else None
            if isinstance(s,list) and s==schedules:
                sem+=1
                if len(b)==len(sched) and hmac.compare_digest(b,sched): byte+=1
        except Exception: pass
if m1>0 and m2>0 and r1 and r2 and sched_valid and len(schedules)==0:
    decision="FILES_ORIGINAL_SCHEDULER_DRIFTED"; reason="HEATER_ORIGINALS_PROVEN_CURRENT_SCHEDULER_EMPTY"
elif m1>0 and m2>0:
    decision="DIAGNOSTIC_BLOCKED"; reason="RETAINED_MATCH_FOUND_ORIGINAL_ROLE_NOT_PROVEN"
else:
    decision="FILES_MIXED_OR_OTHER"; reason="LIVE_FILES_NOT_MATCHED_TO_RETAINED_PREWRITE_MATERIAL"
print(json.dumps({"schema":6,"decision":decision,"reason":reason,"authorization_112_consumed":True,"live_files":{"ordinal_1_retained_byte_match_found":m1>0,"ordinal_2_retained_byte_match_found":m2>0,"ordinal_1_original_role_signal":r1,"ordinal_2_original_role_signal":r2},"scheduler":{"storage_valid":sched_valid,"schedule_count":len(schedules) if sched_valid else None,"retained_semantic_match_found":sem>0,"retained_byte_match_found":byte>0},"retained_material":{"regular_files_scanned":scanned,"unreadable_files":unreadable},"production_apply_authorized":False,"production_mutation":{"home_assistant_config_written":False,"scheduler_service_called":False,"scheduler_storage_written":False,"helper_state_changed":False,"heater_actuated":False,"reload_or_restart":False},"privacy":{"entity_ids_or_targets_emitted":False,"hashes_emitted":False,"latent_schedule_values_emitted":False,"private_paths_emitted":False,"raw_yaml_emitted":False,"schedule_days_or_times_emitted":False,"secret_aliases_emitted":False,"secret_values_emitted":False}},indent=2,sort_keys=True))
PY
