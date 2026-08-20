#!/usr/bin/env bash
set -euo pipefail
exec python -B tools/run_heater_retire_production_gate.py "$@"
