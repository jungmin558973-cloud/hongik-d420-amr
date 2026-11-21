#!/usr/bin/env bash
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"
case "${1:-}" in
  list) exec python3 -B "$root/tray_ports.py" ;;
  start)
    test "$#" -eq 2
    python3 -c 'from tray_ports import validate_port; import sys; validate_port(sys.argv[1])' "$2"
    if systemctl --user cat hongik-tray-monitor.service >/dev/null 2>&1; then
        systemctl --user stop hongik-tray-monitor.service
    fi
    if fuser "$2" >/dev/null 2>&1; then
        printf 'ESP32 포트 사용 중: Arduino Serial Monitor 등 다른 프로그램을 닫아 주세요.\n' >&2
        exit 1
    fi
    systemd-run --user --collect --unit=hongik-tray-monitor --property=Type=exec \
      --property=KillSignal=SIGINT --property=TimeoutStopSec=10 --property=Restart=no \
      --property="StandardOutput=append:$root/tray.log" --property=StandardError=inherit \
      /bin/bash "$root/tray_session.sh" run "$2"
    sleep 1
    if ! systemctl --user is-active --quiet hongik-tray-monitor.service; then
        tail -n 20 "$root/tray.log" >&2
        exit 1
    fi
    ;;
  run)
    source /opt/ros/humble/setup.bash
    export ROS_DOMAIN_ID=120 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
    unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT CYCLONEDDS_URI RMW_FASTRTPS_USE_QOS_FROM_XML
    exec python3 -B "$root/tray_monitor.py" --port "$2"
    ;;
  *) printf 'Usage: tray_session.sh list | start PORT\n' >&2; exit 2 ;;
esac
