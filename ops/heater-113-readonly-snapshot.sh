#!/usr/bin/env bash
set -euo pipefail
umask 077

expected_sha="92b8e71c334edcdbc5dcc45e294b85c4cdb5b51e"
expected_tree="15ef4f98b528d6eef24ff8de7ca368924a21b67d"
expected_parent="95db188766b3d5cd0e149021a2c03cecbf61b5a5"
expected_version="2026.8.2"
repo_url="https://github.com/rozkalnsandris/home-assistant-config.git"

tmp="$(mktemp -d)"
container_tmp=""
container=""
cleanup() {
  if [ -n "$container" ] && [ -n "$container_tmp" ]; then
    docker exec "$container" rm -rf "$container_tmp" >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp"
}
trap cleanup EXIT HUP INT TERM

blocked() {
  python - "$1" <<'PY'
import json, sys
reason=sys.argv[1]
print(json.dumps({
  "schema":1,
  "decision":"DIAGNOSTIC_BLOCKED",
  "reason":reason,
  "production_mutation":{
    "home_assistant_config_written":False,
    "scheduler_service_called":False,
    "scheduler_storage_written":False,
    "helper_state_changed":False,
    "heater_actuated":False,
    "reload_or_restart":False,
  },
  "privacy":{
    "entity_ids_or_targets_emitted":False,
    "hashes_emitted":False,
    "latent_schedule_values_emitted":False,
    "private_paths_emitted":False,
    "raw_yaml_emitted":False,
    "schedule_days_or_times_emitted":False,
    "secret_aliases_emitted":False,
    "secret_values_emitted":False,
  }
}, indent=2, sort_keys=True))
PY
  exit 20
}

git clone -q --depth 2 --branch main "$repo_url" "$tmp/repo" || blocked SOURCE_CLONE_FAILED
cd "$tmp/repo"
actual_sha="$(git rev-parse HEAD 2>/dev/null)" || blocked SOURCE_GIT_PROBE_FAILED
actual_tree="$(git rev-parse 'HEAD^{tree}' 2>/dev/null)" || blocked SOURCE_GIT_PROBE_FAILED
actual_parent="$(git rev-parse 'HEAD^' 2>/dev/null)" || blocked SOURCE_GIT_PROBE_FAILED
[ "$actual_sha" = "$expected_sha" ] || blocked SOURCE_SHA_MISMATCH
[ "$actual_tree" = "$expected_tree" ] || blocked SOURCE_TREE_MISMATCH
[ "$actual_parent" = "$expected_parent" ] || blocked SOURCE_PARENT_MISMATCH
[ "$(cat home-assistant-version.txt 2>/dev/null)" = "$expected_version" ] || blocked EXPECTED_VERSION_FILE_MISMATCH

container="$(python - <<'PY'
from tools.inventory_home_assistant import running_containers, select_container
try:
    print(select_container(running_containers("docker")))
except Exception:
    raise SystemExit(1)
PY
)" || blocked HA_CONTAINER_SELECTION_FAILED
[ -n "$container" ] || blocked HA_CONTAINER_SELECTION_FAILED

running_before="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" || blocked HA_CONTAINER_INSPECT_FAILED
started_before="$(docker inspect -f '{{.State.StartedAt}}' "$container" 2>/dev/null)" || blocked HA_CONTAINER_INSPECT_FAILED
restart_before="$(docker inspect -f '{{.RestartCount}}' "$container" 2>/dev/null)" || blocked HA_CONTAINER_INSPECT_FAILED
[ "$running_before" = "true" ] || blocked HA_NOT_HEALTHY

running_version="$(docker exec "$container" python -m homeassistant --version 2>/dev/null | tr -d '\r\n')" || blocked HA_VERSION_PROBE_FAILED
[ "$running_version" = "$expected_version" ] || blocked HA_VERSION_MISMATCH

container_tmp="$(docker exec "$container" mktemp -d /tmp/ha-heater113-XXXXXXXX 2>/dev/null)" || blocked PRIVATE_TEMP_CREATE_FAILED
[ -n "$container_tmp" ] || blocked PRIVATE_TEMP_CREATE_FAILED

docker exec "$container" mkdir -m 700 -p \
  "$container_tmp/repo/tools" \
  "$container_tmp/repo/packages" >/dev/null 2>&1 || blocked PRIVATE_TEMP_LAYOUT_FAILED

for rel in \
  tools/__init__.py \
  tools/materialize_heater_retire_candidate_privately.py \
  packages/silditajs.yaml \
  packages/heater_scheduler.yaml
 do
  docker cp "$tmp/repo/$rel" "$container:$container_tmp/repo/$rel" >/dev/null 2>&1 || blocked PRIVATE_STAGE_FAILED
 done

snapshot="$tmp/snapshot.json"
if ! docker exec \
  -e "PYTHONPATH=$container_tmp/repo" \
  "$container" python - "$container_tmp/repo" >"$snapshot" 2>/dev/null <<'PY'
import hmac
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))

from homeassistant.util.yaml import Secrets, load_yaml_dict
from tools.materialize_heater_retire_candidate_privately import (
    extract_live_target,
    materialize_candidate_texts,
)

config = Path('/config')
live1 = config / 'packages' / 'silditajs.yaml'
live2 = config / 'packages' / 'heater_scheduler.yaml'
storage = config / '.storage' / 'scheduler.storage'

try:
    loaded = load_yaml_dict(str(live1), Secrets(config))
    private_target = extract_live_target(loaded)
    if private_target is None:
        raise RuntimeError('PRIVATE_LIVE_TARGET_UNRESOLVED')
    c1, c2, _counts = materialize_candidate_texts(
        (repo / 'packages' / 'silditajs.yaml').read_text(encoding='utf-8'),
        (repo / 'packages' / 'heater_scheduler.yaml').read_text(encoding='utf-8'),
        private_target,
    )
    live1_bytes = live1.read_bytes()
    live2_bytes = live2.read_bytes()
    candidate1 = c1.encode('utf-8')
    candidate2 = c2.encode('utf-8')
    candidate_match = [
        hmac.compare_digest(live1_bytes, candidate1),
        hmac.compare_digest(live2_bytes, candidate2),
    ]
except Exception:
    print(json.dumps({"ok": False, "reason": "CANDIDATE_CLASSIFICATION_FAILED"}))
    raise SystemExit(20)

scheduler_valid = False
scheduler_empty = False
scheduler_count = None
try:
    payload = json.loads(storage.read_text(encoding='utf-8'))
    data = payload.get('data') if isinstance(payload, dict) else None
    schedules = data.get('schedules') if isinstance(data, dict) else None
    if isinstance(schedules, list):
        scheduler_valid = True
        scheduler_count = len(schedules)
        scheduler_empty = scheduler_count == 0
except Exception:
    pass

print(json.dumps({
    "ok": True,
    "candidate_match": candidate_match,
    "scheduler_storage_valid": scheduler_valid,
    "scheduler_storage_empty": scheduler_empty,
    "scheduler_schedule_count": scheduler_count,
}, sort_keys=True))
PY
then
  blocked CONTAINER_SNAPSHOT_FAILED
fi

check_config_passed=false
if docker exec "$container" python -m homeassistant --script check_config --config /config --fail-on-warnings >/dev/null 2>&1; then
  check_config_passed=true
fi

running_after="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" || blocked HA_CONTAINER_INSPECT_FAILED
started_after="$(docker inspect -f '{{.State.StartedAt}}' "$container" 2>/dev/null)" || blocked HA_CONTAINER_INSPECT_FAILED
restart_after="$(docker inspect -f '{{.RestartCount}}' "$container" 2>/dev/null)" || blocked HA_CONTAINER_INSPECT_FAILED
runtime_unchanged=false
if [ "$running_after" = "true" ] && [ "$started_before" = "$started_after" ] && [ "$restart_before" = "$restart_after" ]; then
  runtime_unchanged=true
fi

# Explicitly clean only our private /tmp staging before reporting.
docker exec "$container" rm -rf "$container_tmp" >/dev/null 2>&1 || blocked PRIVATE_TEMP_CLEANUP_FAILED
container_tmp=""

python - "$snapshot" "$check_config_passed" "$runtime_unchanged" "$actual_sha" "$actual_tree" "$actual_parent" "$expected_version" <<'PY'
import json
import sys
from pathlib import Path

snap = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
check_config_passed = sys.argv[2] == 'true'
runtime_unchanged = sys.argv[3] == 'true'
sha, tree, parent, version = sys.argv[4:8]

if not snap.get('ok'):
    decision = 'DIAGNOSTIC_BLOCKED'
    reason = snap.get('reason', 'SNAPSHOT_INVALID')
elif not runtime_unchanged:
    decision = 'HA_NOT_HEALTHY'
    reason = 'HA_RUNTIME_CHANGED_DURING_READ_ONLY_SNAPSHOT'
else:
    matches = snap.get('candidate_match')
    if matches == [True, True]:
        if snap.get('scheduler_storage_valid') and not snap.get('scheduler_storage_empty'):
            decision = 'FILES_CANDIDATE_SCHEDULER_DRIFTED'
            reason = None
        elif snap.get('scheduler_storage_valid') and snap.get('scheduler_storage_empty'):
            decision = 'DIAGNOSTIC_BLOCKED'
            reason = 'FILES_CANDIDATE_BUT_SCHEDULER_EMPTY_UNEXPECTED_AFTER_INCIDENT'
        else:
            decision = 'DIAGNOSTIC_BLOCKED'
            reason = 'FILES_CANDIDATE_SCHEDULER_STORAGE_INVALID'
    elif matches in ([True, False], [False, True]):
        decision = 'FILES_MIXED_OR_OTHER'
        reason = 'MIXED_CANDIDATE_IDENTITY'
    elif matches == [False, False]:
        decision = 'DIAGNOSTIC_BLOCKED'
        reason = 'LIVE_FILES_NOT_CANDIDATE_NEED_RETAINED_ORIGINAL_COMPARE'
    else:
        decision = 'DIAGNOSTIC_BLOCKED'
        reason = 'CANDIDATE_CLASSIFICATION_INVALID'

report = {
    'schema': 1,
    'decision': decision,
    'source_gate': {
        'sha': sha,
        'tree': tree,
        'parent': parent,
    },
    'home_assistant': {
        'version': version,
        'running': True,
        'runtime_unchanged_during_snapshot': runtime_unchanged,
        'live_check_config_passed': check_config_passed,
    },
    'live_files': {
        'ordinal_1_equals_candidate': snap.get('candidate_match', [None, None])[0],
        'ordinal_2_equals_candidate': snap.get('candidate_match', [None, None])[1],
    },
    'scheduler': {
        'storage_valid': snap.get('scheduler_storage_valid'),
        'storage_empty': snap.get('scheduler_storage_empty'),
        'schedule_count': snap.get('scheduler_schedule_count'),
    },
    'private_temp_cleanup': True,
    'authorization_112_consumed': True,
    'production_apply_authorized': False,
    'production_mutation': {
        'home_assistant_config_written': False,
        'scheduler_service_called': False,
        'scheduler_storage_written': False,
        'helper_state_changed': False,
        'heater_actuated': False,
        'reload_or_restart': False,
    },
    'privacy': {
        'entity_ids_or_targets_emitted': False,
        'hashes_emitted': False,
        'latent_schedule_values_emitted': False,
        'private_paths_emitted': False,
        'raw_yaml_emitted': False,
        'schedule_days_or_times_emitted': False,
        'secret_aliases_emitted': False,
        'secret_values_emitted': False,
    },
}
if reason is not None:
    report['reason'] = reason
print(json.dumps(report, indent=2, sort_keys=True))
PY
