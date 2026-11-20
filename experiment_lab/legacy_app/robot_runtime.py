"""Run robot-side utilities directly when the console itself runs on Jetson."""
import os
import shlex


def local_robot():return os.environ.get('D420_LOCAL_ROBOT')=='1'


def command(host,args,ssh_options=None):
    if local_robot():return list(args)
    options=ssh_options or ['-n','-o','BatchMode=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=6']
    return ['ssh',*options,host,shlex.join(args)]
