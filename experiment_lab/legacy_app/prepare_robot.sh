#!/usr/bin/env bash
# Runs ONLY on an operator's real-robot connect click. No nonzero commands.
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
remote_host="${1:-${D420_HOST:-seo@192.168.0.47}}"
mapping_mode=0
if [ "${2:-}" = '--mapping' ]; then mapping_mode=1; fi
bridge_root="$HOME/d420_ros_integrated"
for source_file in d420_core.py d420_ros_bridge.py; do
    if ! test -r "$bridge_root/$source_file"; then
        printf '차체 브리지 소스가 없습니다: %s\n' "$bridge_root/$source_file" >&2
        exit 1
    fi
done
ssh_args=(-o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o HostKeyAlias=192.168.0.12 -o StrictHostKeyChecking=yes)
ssh "${ssh_args[@]}" "$remote_host" 'bash -s' <<'REMOTE'
set -eo pipefail
source /opt/ros/humble/setup.bash
source /home/seo/d420_ws/install/setup.bash
export ROS_DOMAIN_ID=120 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT CYCLONEDDS_URI RMW_FASTRTPS_USE_QOS_FROM_XML
# Stop autonomous command sources first. An open WASD publisher will fail the guard.
for unit in hongik-goal-click.service hongik-zone-nav.service hongik-waypoint-nav.service; do
    if systemctl --user cat "$unit" >/dev/null 2>&1; then systemctl --user stop "$unit"; fi
done
python3 - <<'PY'
import time, rclpy
rclpy.init()
n=rclpy.create_node('hongik_scenario_prepare_check')
try:
    end=time.monotonic()+3
    while time.monotonic()<end: rclpy.spin_once(n,timeout_sec=0.1)
    if n.count_publishers('/d420/manual_cmd_vel'):
        raise SystemExit('기존 WASD 창을 Q로 닫아 주세요. 다른 명령 발행자가 있습니다.')
finally:
    n.destroy_node()
    rclpy.shutdown()
PY
# Preserve a running SLAM map before resetting odometry. Do not overwrite archives.
if pgrep -f '^/opt/ros/humble/lib/slam_toolbox/async_slam_toolbox_node' >/dev/null; then
    saver=/home/seo/d420_ws/experiments/manual_mapping_20261024/save.sh
    if ! test -f "$saver"; then printf '현재 SLAM 지도를 먼저 저장해야 합니다.\n' >&2; exit 1; fi
    timeout 60 bash "$saver" --before-manual
fi
for unit in hongik-test-zone.service hongik-saved-nav.service hongik-mapping.service hongik-robot.service hongik-scenarios.service; do
    if systemctl --user cat "$unit" >/dev/null 2>&1; then systemctl --user stop "$unit"; fi
done
destination=/home/seo/d420_ws/scenario_lab
if test -d "$destination/jetson"; then
    mkdir -p "$destination/backups"
    cp -a "$destination/jetson" "$destination/backups/jetson_$(date +%Y%m%d_%H%M%S)_$$"
fi
mkdir -p "$destination"
bridge_backup="$destination/backups/bridge_$(date +%Y%m%d_%H%M%S)_$$"
mkdir -p "$bridge_backup"
cp -p /home/seo/d420_ros_integrated/d420_core.py /home/seo/d420_ros_integrated/d420_ros_bridge.py "$bridge_backup/"
REMOTE
scp "${ssh_args[@]}" "$root/engine.py" "$root/depth_route.py" "$remote_host:/home/seo/d420_ws/scenario_lab/"
scp "${ssh_args[@]}" -r "$root/jetson" "$remote_host:/home/seo/d420_ws/scenario_lab/"
scp "${ssh_args[@]}" "$bridge_root/d420_core.py" "$bridge_root/d420_ros_bridge.py" "$remote_host:/home/seo/d420_ros_integrated/"
ssh "${ssh_args[@]}" "$remote_host" "D420_MAPPING_MODE=$mapping_mode bash -s" <<'REMOTE'
set -eo pipefail
root=/home/seo/d420_ws/scenario_lab/jetson
python3 /home/seo/d420_ros_integrated/d420_core.py
if ! systemd-run --user --collect --unit=hongik-scenarios --setenv="D420_MAPPING_MODE=${D420_MAPPING_MODE:-0}" --property=Type=exec --property=KillSignal=SIGINT --property=KillMode=control-group --property=TimeoutStopSec=30 --property=Restart=no --property="StandardOutput=append:$root/session.log" --property=StandardError=inherit /bin/bash "$root/start.sh"; then
    systemctl --user start hongik-robot.service
    exit 1
fi
printf 'Scenario sensors + motor bridge started. No movement goal has been sent.\n'
REMOTE
