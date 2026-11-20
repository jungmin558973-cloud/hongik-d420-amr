#!/usr/bin/env bash
# Explicit commissioning entry point; never invoked by installer or Windows UI.
set -euo pipefail
if [ "${1:-}" != --operator-at-robot ]; then
  echo 'Requires --operator-at-robot PORT after verifying old serial owner is stopped.'; exit 1
fi
: "${2:?Supply actual existing serial device path}"
: "${D420_VALIDATION_FILE:?Supply local commissioning record path}"
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=120 D420_EXPERIMENT_MOTION=1
if fuser "$2" >/dev/null 2>&1; then echo 'Serial device already has an owner; refused'; exit 1; fi
exec /usr/bin/python3 jetson_experiment_bridge.py --port "$2" --allow-motion --max-linear-speed "${D420_BRIDGE_MAX_SPEED:-0.12}"
