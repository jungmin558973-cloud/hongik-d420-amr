#!/usr/bin/env bash
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source /opt/ros/humble/setup.bash
source "$root/../install/setup.bash"
export ROS_DOMAIN_ID=120 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT CYCLONEDDS_URI RMW_FASTRTPS_USE_QOS_FROM_XML
if [ "${1:-}" = '--run' ]; then exec python3 -B "$root/rviz_desktop.py"; fi
if systemctl --user is-active --quiet hongik-rviz-display; then exit 0; fi
mkdir -p "$root/runs/rviz"
systemd-run --user --collect --unit=hongik-rviz-display --property=Type=exec --property=KillSignal=SIGTERM --property=TimeoutStopSec=15 --property="StandardOutput=append:$root/runs/rviz/session.log" --property=StandardError=inherit /bin/bash "$root/rviz_start.sh" --run
