#!/usr/bin/env bash
# Starts isolated Nav2 output only. The operator app alone owns motor publishing.
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source /opt/ros/humble/setup.bash
source /home/seo/d420_ws/install/setup.bash
export ROS_DOMAIN_ID=120 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT CYCLONEDDS_URI RMW_FASTRTPS_USE_QOS_FROM_XML
mode="${D420_NAV_MODE:-saved-map}"
if [ "${1:-}" = '--mapless' ]; then mode=mapless; fi
if [ "${1:-}" = '--run' ]; then exec ros2 launch "$root/nav2.launch.py" drive:=false mode:="$mode"; fi
if systemctl --user is-active --quiet hongik-waypoint-nav; then
    current="$(systemctl --user show hongik-waypoint-nav --property=Environment --value)"
    if [[ "$current" != *"D420_NAV_MODE=$mode"* ]]; then
        printf '다른 Nav2 모드가 실행 중입니다. 정지 상태에서 기존 서비스를 먼저 종료하세요.\n' >&2; exit 1
    fi
    exit 0
fi
systemd-run --user --collect --unit=hongik-waypoint-nav --setenv="D420_NAV_MODE=$mode" --property=Type=exec --property=KillSignal=SIGINT --property=TimeoutStopSec=15 --property="StandardOutput=append:$root/session.log" --property=StandardError=inherit /bin/bash "$root/start.sh" --run
