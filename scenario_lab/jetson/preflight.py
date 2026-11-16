#!/usr/bin/env python3
"""Check device identity, ownership and duplicate ROS nodes. Never write serial."""
from pathlib import Path
import subprocess
import time

import rclpy


D420 = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
LIDAR = ('/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_'
         '4015c9a5201ef111a8bdc9e40f0f12f8-if00-port0')
DEVICES = [(D420, '1a86', '7523'), (LIDAR, '10c4', 'ea60')]


def check_devices():
    deadline = time.monotonic() + 60
    while any(not Path(port).exists() for port, _, _ in DEVICES):
        if time.monotonic() >= deadline:
            raise RuntimeError('D420/C1 USB not ready after 60 seconds; reconnect the devices.')
        time.sleep(2)
    for port, vendor, product in DEVICES:
        device = Path(port).resolve()
        sys_device = (Path('/sys/class/tty') / device.name / 'device').resolve()
        for ancestor in [sys_device, *sys_device.parents]:
            if (ancestor / 'idVendor').exists():
                identity = ((ancestor / 'idVendor').read_text().strip(),
                            (ancestor / 'idProduct').read_text().strip())
                if identity != (vendor, product):
                    raise RuntimeError(f'{port}: unexpected USB identity {identity}')
                break
        else:
            raise RuntimeError(f'{port}: no USB identity found')
        result = subprocess.run(['fuser', str(device)], capture_output=True, text=True)
        if result.returncode == 0:
            raise RuntimeError(f'{device} is already owned by PID(s) {result.stdout.strip()}')
        if result.returncode != 1:
            raise RuntimeError(f'Cannot check {device}: {result.stderr.strip()}')
        print(f'USB verified: {vendor}:{product} -> {device}', flush=True)


def check_ros():
    forbidden = {'/amcl', '/slam_toolbox', '/d420_ros_bridge', '/sllidar_node',
                 '/d420_state_publisher', '/camera/camera'}
    rclpy.init()
    node = rclpy.create_node('hongik_boot_preflight')
    try:
        end = time.monotonic() + 4
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.1)
        present = {f'{namespace.rstrip("/")}/{name}'
                   for name, namespace in node.get_node_names_and_namespaces()}
        duplicates = present & forbidden
        if duplicates:
            raise RuntimeError(f'Existing robot nodes must be stopped first: {sorted(duplicates)}')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    check_devices()
    check_ros()
    print('Scenario device preflight passed. Waiting for operator trajectory requests.', flush=True)
