#!/usr/bin/env bash
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=120 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT CYCLONEDDS_URI RMW_FASTRTPS_USE_QOS_FROM_XML
engine=/home/seo/d420_ws/scenario_lab/models/yolo11n_416_fp16.engine
test -s "$engine" || { printf 'YOLO TensorRT 엔진이 없습니다. 모델 준비가 필요합니다.\n' >&2; exit 1; }
# The stack start response precedes camera node discovery after a reboot.
python3 - <<'PY'
import rclpy,time
rclpy.init();node=rclpy.create_node('hongik_wait_follow_camera')
try:
    end=time.monotonic()+20
    while time.monotonic()<end:
        rclpy.spin_once(node,timeout_sec=.2)
        if any(name=='/camera/camera/set_parameters' for name,types in node.get_service_names_and_types()):break
    else:raise SystemExit('카메라 노드가 준비되지 않았습니다. 실차 센서 연결을 확인해 주세요.')
finally:node.destroy_node();rclpy.shutdown()
PY
ros2 param set /camera/camera enable_depth true
ros2 param set /camera/camera enable_sync true
ros2 param set /camera/camera align_depth.enable true
if systemctl --user is-active --quiet hongik-person-detector; then
    systemctl --user stop hongik-person-detector
fi
systemd-run --user --collect --unit=hongik-person-detector --property=Type=exec --property=KillSignal=SIGINT --property=Restart=no /bin/bash "$root/follow_start.sh"
