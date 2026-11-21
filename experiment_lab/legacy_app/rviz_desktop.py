#!/usr/bin/env python3
"""Supervise RViz on an isolated X display; never share the operator's desktop."""
import os
from pathlib import Path
import secrets
import signal
import platform
import shutil
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parent


def main():
    runtime=ROOT/'vendor'/('rviz-runtime-arm64' if platform.machine() in ('aarch64','arm64') else 'rviz-runtime')
    def binary(name):
        installed=shutil.which(name)
        candidate=runtime/'usr/bin'/name
        if installed:return installed
        if candidate.exists():return str(candidate)
        raise RuntimeError(name+'가 없습니다. RViz 표시 패키지를 설치해 주세요.')
    folder=ROOT/'runs/rviz';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    display=next(n for n in range(110,130) if not Path(f'/tmp/.X11-unix/X{n}').exists())
    auth=folder/'Xauthority';auth.touch(mode=0o600,exist_ok=True)
    subprocess.run(['xauth','-f',str(auth),'add',f':{display}','.',secrets.token_hex(16)],check=True)
    env={**os.environ,'DISPLAY':f':{display}','XAUTHORITY':str(auth),'QT_QPA_PLATFORM':'xcb',
         'LIBGL_ALWAYS_SOFTWARE':'1','PATH':str(runtime/'usr/bin')+':'+os.environ.get('PATH',''),'LD_LIBRARY_PATH':str(runtime/('usr/lib/aarch64-linux-gnu' if platform.machine() in ('aarch64','arm64') else 'usr/lib/x86_64-linux-gnu'))+':'+os.environ.get('LD_LIBRARY_PATH','')}
    processes=[]
    def stop(*_):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        processes.append(subprocess.Popen([binary('Xvfb'),f':{display}','-screen','0','1280x800x24',
            '-nolisten','tcp','-auth',str(auth),'-noreset'],env=env))
        for _ in range(50):
            if Path(f'/tmp/.X11-unix/X{display}').exists():break
            if processes[0].poll() is not None:raise RuntimeError('RViz virtual display failed')
            time.sleep(.1)
        processes.append(subprocess.Popen(['rviz2','-d',str(ROOT/'rviz_mapping.rviz')],env=env))
        processes.append(subprocess.Popen([binary('x11vnc'),'-display',f':{display}','-auth',str(auth),
            '-localhost','-rfbport','5908','-nopw','-forever','-shared','-noxdamage','-wait','80','-defer','40',
            '-nosel','-noclipboard','-noprimary','-quiet'],env=env))
        while True:
            if any(p.poll() is not None for p in processes):raise RuntimeError('RViz display component exited')
            time.sleep(.5)
    finally:
        for p in reversed(processes):
            if p.poll() is None:p.terminate()
        for p in reversed(processes):
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill()


if __name__=='__main__':main()
