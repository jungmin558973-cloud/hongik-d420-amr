#!/usr/bin/env bash
# One-time setup. No movement goal is sent; existing maps and units are backed up.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ "$(id -un)" != seo ] || [ "$(hostname)" != SJM ]; then printf 'Jetson SJM의 seo 계정에서 실행하세요.\n' >&2; exit 1; fi
set +u
source /opt/ros/humble/setup.bash
set -u
python3 -c 'import aiohttp,rclpy,PIL,yaml,numpy'
if [ "$(loginctl show-user seo -p Linger --value)" != yes ]; then
    loginctl enable-linger seo || {
        printf '자동 부팅을 위해 Jetson에서 sudo loginctl enable-linger seo 를 한 번 실행하세요.\n' >&2
        exit 1
    }
fi
mkdir -p "$HOME/.config/systemd/user" "$root/../runs"
backup="$root/../backups/team_access_$(date +%Y%m%d_%H%M%S)_$$"
mkdir -p "$backup"
if systemctl --user is-active --quiet hongik-scenarios.service; then
    # Keep the existing stack while saving. This is not a switch to a new manual
    # bridge: the idle web app legitimately owns a command publisher already.
    timeout 60 bash "$HOME/d420_ws/experiments/manual_mapping_20261024/save.sh"
fi
for unit in hongik-robot hongik-scenarios hongik-depth-observer hongik-scenario-ui hongik-team-ui; do
    systemctl --user cat "$unit.service" > "$backup/$unit.service" 2>/dev/null || true
    systemctl --user stop "$unit.service" 2>/dev/null || true
done
systemctl --user disable hongik-robot.service hongik-scenario-ui.service 2>/dev/null || true
for unit in hongik-scenarios hongik-depth-observer hongik-team-ui; do
    install -m 644 "$root/systemd/$unit.service" "$HOME/.config/systemd/user/$unit.service"
done
systemctl --user daemon-reload
systemd-analyze --user verify "$HOME/.config/systemd/user/hongik-scenarios.service" "$HOME/.config/systemd/user/hongik-depth-observer.service" "$HOME/.config/systemd/user/hongik-team-ui.service"
systemctl --user enable --now hongik-scenarios.service hongik-depth-observer.service hongik-team-ui.service
systemctl --user is-enabled hongik-scenarios.service hongik-depth-observer.service hongik-team-ui.service
printf '앱은 127.0.0.1:8765 에서만 수신합니다. Windows 실행 파일의 SSH 터널로 접속하세요.\n'
