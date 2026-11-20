#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=120
: "${D420_AGENT_TOKEN:?Set a random token of at least 32 characters}"
exec /usr/bin/python3 -m lab.agent --root "${D420_JETSON_DATA_ROOT:-$HOME/hongik_experiment_data}"
