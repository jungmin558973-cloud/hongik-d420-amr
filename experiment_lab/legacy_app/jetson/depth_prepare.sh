#!/usr/bin/env bash
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if systemctl --user is-active --quiet hongik-depth-observer.service; then exit 0; fi
systemd-run --user --collect --unit=hongik-depth-observer --property=Type=exec --property=KillSignal=SIGINT --property=TimeoutStopSec=10 --property="StandardOutput=append:$root/depth_observer.log" --property=StandardError=inherit /bin/bash "$root/depth_start.sh"
