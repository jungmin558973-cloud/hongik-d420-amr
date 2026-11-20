#!/usr/bin/env bash
# Read-only preflight. Never starts another serial owner.
set -euo pipefail
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=120
ros2 node list
ros2 topic list -t
ros2 topic info /odom --verbose
ros2 topic info /scan --verbose
ros2 topic info /d420/depth_route/observation --verbose
printf '\nInspect the existing service/serial owner before installing the extension.\n'
systemctl --user cat hongik-scenarios.service || true
