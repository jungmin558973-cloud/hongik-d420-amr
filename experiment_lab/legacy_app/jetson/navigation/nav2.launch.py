"""Nav2 planning/control with isolated output; the local console owns the motors."""
from pathlib import Path
import time
import sys
import tempfile
import atexit
sys.path.insert(0,str(Path(__file__).resolve().parent))
from mapless_config import parameters as mapless_parameters

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def build(context):
    value = LaunchConfiguration("drive").perform(context).lower()
    if value not in ["true", "false"]:
        raise RuntimeError("drive must be true or false")
    drive = value == "true"
    if drive:
        raise RuntimeError("This app uses its existing single command publisher; direct Nav2 motor output is disabled")
    topic = "/d420/manual_cmd_vel" if drive else "/d420/nav_preview_cmd_vel"
    print(f"D420 trial: drive={drive}; controller output={topic}; no goal sent")
    root = Path(__file__).resolve().parent
    defaults = yaml.safe_load(
        (Path(get_package_share_directory("nav2_bringup"))/"params/nav2_params.yaml").read_text()
    )
    mode=LaunchConfiguration('mode').perform(context)
    if mode not in ('saved-map','mapless'):raise RuntimeError('Unknown navigation mode')
    parameter_file=root/'params.yaml'
    if mode=='mapless':
        config=mapless_parameters(yaml.safe_load(parameter_file.read_text()))
        with tempfile.NamedTemporaryFile(mode='w',prefix='hongik_mapless_',suffix='.yaml',delete=False) as f:
            yaml.safe_dump(config,f);parameter_file=Path(f.name)
        atexit.register(lambda:parameter_file.unlink(missing_ok=True))
    nodes = []
    for package, executable in [
        ("nav2_planner", "planner_server"),
        ("nav2_controller", "controller_server"),
        ("nav2_bt_navigator", "bt_navigator"),
    ]:
        params = [str(parameter_file)]
        if executable == "bt_navigator":
            params.append({
                "plugin_lib_names": defaults["bt_navigator"]["ros__parameters"]["plugin_lib_names"],
                "default_nav_to_pose_bt_xml": str(root/"navigate.xml"),
                "default_nav_through_poses_bt_xml": str(root/"navigate_through.xml"),
            })
        nodes.append(Node(
            package=package, executable=executable, name=executable, namespace="d420_nav",
            output="screen", parameters=params, remappings=[("cmd_vel", topic)],
        ))
    nodes.append(Node(
        package="nav2_lifecycle_manager", executable="lifecycle_manager",
        name="lifecycle_manager", namespace="d420_nav", output="screen",
        parameters=[{"use_sim_time": False, "autostart": True,
                     "node_names": ["planner_server", "controller_server", "bt_navigator"],
                     "bond_timeout": 4.0}],
    ))
    return nodes


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("drive", default_value="false"),
        DeclareLaunchArgument("mode", default_value="saved-map"),
        OpaqueFunction(function=build),
    ])
