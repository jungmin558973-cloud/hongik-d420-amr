#!/usr/bin/env python3
"""Existing serial bridge with mapping-only forward odometry correction."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path.home()/'d420_ros_integrated'))
import serial_bridge as bridge
from mapping_odometry import forward_decoder
from local_route_host import LocalRouteAdapter

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',required=True)
    parser.add_argument('--allow-motion',action='store_true')
    parser.add_argument('--max-linear-speed',type=float,default=.30)
    parser.add_argument('--max-angular-speed',type=float,default=.30)
    args=parser.parse_args()
    bridge.Decoder=forward_decoder(bridge.Decoder)
    bridge.run(args.port,args.allow_motion,max_linear_speed=args.max_linear_speed,max_angular_speed=args.max_angular_speed,local_route_factory=LocalRouteAdapter)
