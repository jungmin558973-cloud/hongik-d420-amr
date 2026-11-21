"""Scenario sensor stack: RGB + display-only SLAM. No AMCL/Nav2 or automatic trajectory."""
from pathlib import Path
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, RegisterEventHandler, EmitEvent, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch_ros.actions import Node


def on_exit(event, context):
    if context.is_shutdown:
        return []
    return [EmitEvent(event=Shutdown(reason=f'Scenario component exited: {event.process_name}'))]


def generate_launch_description():
    root = Path(get_package_share_directory('d420_description'))
    camera = Path(get_package_share_directory('realsense2_camera'))
    bridge=(Path(__file__).parent/'mapping_bridge.py' if os.environ.get('D420_MAPPING_MODE')=='1'
            else Path.home()/'d420_ros_integrated/d420_ros_bridge.py')
    return LaunchDescription([
        RegisterEventHandler(OnProcessExit(on_exit=on_exit)),
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             name='d420_state_publisher', output='screen',
             parameters=[{'robot_description': (root/'urdf/d420.urdf').read_text()}]),
        ExecuteProcess(cmd=['/usr/bin/python3', '-B', str(bridge),
                            '--port', '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0', '--allow-motion',
                            '--max-linear-speed', '1.5' if os.environ.get('D420_MAPPING_MODE')=='1' else '0.3',
                            '--max-angular-speed', '5.4' if os.environ.get('D420_MAPPING_MODE')=='1' else '0.3'], output='screen'),
        Node(package='sllidar_ros2', executable='sllidar_node', name='sllidar_node', output='screen',
             parameters=[{'channel_type': 'serial', 'serial_port': '/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_4015c9a5201ef111a8bdc9e40f0f12f8-if00-port0',
                          'serial_baudrate': 460800, 'frame_id': 'laser', 'inverted': False,
                          'angle_compensate': True, 'scan_mode': 'Standard'}]),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(camera/'launch/rs_launch.py')),
            launch_arguments={'enable_color':'true','enable_depth':'true','align_depth.enable':'true','enable_sync':'true',
                              'config_file':str(Path(__file__).parent/'camera.yaml'),
                              'enable_gyro':'false','enable_accel':'false',
                              'depth_module.depth_profile':'848,480,30',
                              'rgb_camera.color_profile':'640,480,30','publish_tf':'true'}.items()),
        ExecuteProcess(cmd=['/usr/bin/python3','-B',str(Path(__file__).parent/'camera_relay.py')],output='screen'),
        TimerAction(period=3.0,actions=[Node(package='slam_toolbox',executable='async_slam_toolbox_node',
             name='slam_toolbox',output='screen',parameters=[str(Path(__file__).parent/'slam.yaml'),
                                                           {'use_sim_time':False}])]),
    ])
