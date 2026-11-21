"""Replacement entry point for the SAME serial owner, opt-in installation only."""
from pathlib import Path
import argparse
import sys
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/'legacy_app/jetson'),str(root/'legacy_app'),str(Path.home()/'d420_ros_integrated')]
# The copied bridge contains the ownership hook; core/decoder protocol is reused.
import importlib.util
spec=importlib.util.spec_from_file_location('lab_serial_bridge',root/'legacy_app/jetson/serial_bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
from mapping_odometry import forward_decoder
from local_route_host import LocalRouteAdapter
from lab.jetson_adapter import ExperimentAdapter


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',required=True);p.add_argument('--allow-motion',action='store_true')
    p.add_argument('--max-linear-speed',type=float,default=.12);p.add_argument('--max-angular-speed',type=float,default=.3)
    a=p.parse_args();bridge.Decoder=forward_decoder(bridge.Decoder)
    bridge.run(a.port,a.allow_motion,max_linear_speed=a.max_linear_speed,max_angular_speed=a.max_angular_speed,
               local_route_factory=lambda node:ExperimentAdapter(node,LocalRouteAdapter(node)))
