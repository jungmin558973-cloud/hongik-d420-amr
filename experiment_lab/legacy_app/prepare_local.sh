#!/usr/bin/env bash
# Jetson-hosted console: no SSH loopback and no automatic movement goal.
set -euo pipefail
if [ "${D420_LOCAL_ROBOT:-}" != 1 ] || [ "$(hostname)" != SJM ]; then
    printf 'Jetson 로컬 앱에서만 실행합니다.\n' >&2; exit 1
fi
if [ "${1:-}" = --new-map ] && systemctl --user is-active --quiet hongik-scenarios.service; then
    timeout 60 bash "$HOME/d420_ws/experiments/manual_mapping_20261024/save.sh" --before-manual
    systemctl --user restart hongik-scenarios.service
else
    systemctl --user start hongik-scenarios.service
fi
