#!/usr/bin/env bash
# Open the local operator console, optionally in a standalone browser window.
set -eo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
url=http://127.0.0.1:8765
app_window=false
case "${1:-}" in
    --app) app_window=true ;;
    --follow) app_window=true; url='http://127.0.0.1:8765/?drive=follow&mode=real&camera=full' ;;
    '') ;;
    *) printf '사용법: %s [--app|--follow]\n' "$0" >&2; exit 2 ;;
esac
open_window() {
    if "$app_window"; then
        for browser in google-chrome chromium chromium-browser; do
            if command -v "$browser" >/dev/null 2>&1; then
                mkdir -p "$root/runs"
                browser_args=(--app="$url" --class=HongikScenarioLab \
                    --user-data-dir="$root/runs/app-profile" --window-size=1440,1000 \
                    --no-first-run --no-default-browser-check)
                if systemctl --user show-environment >/dev/null 2>&1; then
                    browser_environment=()
                    for key in DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR; do
                        if [ -n "${!key:-}" ]; then browser_environment+=("--setenv=$key=${!key}"); fi
                    done
                    systemd-run --user --collect --quiet --property=Type=exec \
                        --property="StandardOutput=append:$root/runs/app-window.log" \
                        --property=StandardError=inherit "${browser_environment[@]}" \
                        "$browser" "${browser_args[@]}"
                else
                    nohup "$browser" "${browser_args[@]}" \
                        >"$root/runs/app-window.log" 2>&1 </dev/null &
                fi
                return 0
            fi
        done
    fi
    xdg-open "$url" >/dev/null 2>&1 || printf '열기: %s\n' "$url"
}
ready() {
    python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/api/session",timeout=0.5).read()' >/dev/null 2>&1
}
if ready; then
    open_window
    exit 0
fi
mkdir -p "$root/runs"
if systemctl --user show-environment >/dev/null 2>&1; then
    if ! systemctl --user is-active --quiet hongik-scenario-ui.service; then
        service_environment=()
        if [ -n "${SSH_AUTH_SOCK:-}" ]; then service_environment+=("--setenv=SSH_AUTH_SOCK=$SSH_AUTH_SOCK"); fi
        if [ -n "${D420_HOST:-}" ]; then service_environment+=("--setenv=D420_HOST=$D420_HOST"); fi
        systemd-run --user --collect --unit=hongik-scenario-ui --property=Type=exec \
            --property=KillSignal=SIGINT --property=TimeoutStopSec=15 --property=Restart=no \
            --property="StandardOutput=append:$root/runs/server.log" --property=StandardError=inherit \
            "${service_environment[@]}" /bin/bash "$root/start.sh" --allow-real
    fi
    for attempt in {1..50}; do
        if ready; then
            printf 'HONGIK CAFÉ: %s\n서버 종료: systemctl --user stop hongik-scenario-ui.service\n' "$url"
            open_window
            exit 0
        fi
        sleep 0.2
    done
    printf '서버 로그: %s\n' "$root/runs/server.log" >&2
    exit 1
fi
bash "$root/start.sh" --allow-real >"$root/runs/server.log" 2>&1 &
lab_server_pid=$!
cleanup() {
    if kill -0 "$lab_server_pid" 2>/dev/null; then
        kill -INT "$lab_server_pid" 2>/dev/null || true
        wait "$lab_server_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
for attempt in {1..50}; do
    if ready; then
        printf 'HONGIK CAFÉ: %s\n이 터미널을 열어 두세요. 종료: Ctrl+C\n' "$url"
        open_window
        wait "$lab_server_pid"
        exit $?
    fi
    if ! kill -0 "$lab_server_pid" 2>/dev/null; then cat "$root/runs/server.log" >&2; exit 1; fi
    sleep 0.2
done
printf '서버 시작 시간 초과. 로그: %s\n' "$root/runs/server.log" >&2
exit 1
