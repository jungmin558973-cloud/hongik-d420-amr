#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
test -f /opt/ros/humble/setup.bash || { echo 'ROS2 Humble required'; exit 1; }
sudo apt-get update
sudo apt-get install -y python3-flask python3-serial ros-humble-rosbag2-py ros-humble-rosidl-runtime-py
python3 -m compileall -q lab legacy_app/jetson/serial_bridge.py
printf 'Installed read-only dependencies. No services modified, no serial device opened.\n'
