#!/usr/bin/env bash
# Deploy/start perception only. No velocity publisher and no serial port access.
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
remote_host="${1:-hongik-jetson}"
ssh_args=(-o BatchMode=yes -o UpdateHostKeys=no -o ConnectTimeout=5 -o ConnectionAttempts=1 -o HostKeyAlias=192.168.0.12 -o StrictHostKeyChecking=yes)
ssh "${ssh_args[@]}" "$remote_host" 'mkdir -p /home/seo/d420_ws/scenario_lab/jetson'
scp "${ssh_args[@]}" "$root/jetson/person_detector.py" "$root/jetson/person_observation.py" "$root/jetson/yolo_runtime.py" "$root/jetson/camera_frame.py" "$root/jetson/follow_session.sh" "$root/jetson/follow_start.sh" "$remote_host:/home/seo/d420_ws/scenario_lab/jetson/"
ssh "${ssh_args[@]}" "$remote_host" 'bash /home/seo/d420_ws/scenario_lab/jetson/follow_session.sh'
