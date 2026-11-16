#requires -Version 5.1
# Robot maintainer only. Team members normally use Install/Open-HongikCafe.cmd.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Connection.ps1')
try {
    $stateRoot = Join-Path $env:LOCALAPPDATA 'HongikCafe'
    $config = Get-HongikConfig $PSScriptRoot $stateRoot
    $sshConfig = New-HongikSshConfig $PSScriptRoot $stateRoot ([string]$config.jetson_address) ([int]$config.local_port)
    $ssh = Get-Command ssh.exe -ErrorAction Stop
    $scp = Get-Command scp.exe -ErrorAction Stop
    $payload = Join-Path $PSScriptRoot 'hongik-team-app.tar.gz'
    if (-not (Test-Path -LiteralPath $payload)) { throw 'hongik-team-app.tar.gz가 없습니다. ZIP 전체를 압축 해제하세요.' }
    $options = @('-F', $sshConfig, '-o', 'ClearAllForwardings=yes')
    Write-Host 'Jetson 담당자용 설치입니다. 실행 중인 주행이 있으면 앱에서 먼저 정지하세요.'
    Write-Host 'SSH 창에서 seo 계정 암호가 필요할 수 있습니다. 지도와 기존 코드는 백업합니다.'
    & $scp.Source @options $payload 'hongik-cafe:/tmp/hongik-team-app.tar.gz'
    if ($LASTEXITCODE -ne 0) { throw '설치 파일 전송 실패. Jetson 전원과 K221 연결을 확인하세요.' }
    $remote = @'
set -euo pipefail
python3 - <<'CHECK'
import json, subprocess, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=3) as response:
        state = json.load(response)
except OSError:
    if subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'hongik-team-ui.service']).returncode == 0:
        raise SystemExit('UI is running but status is unavailable. Stop and check the app before installing.')
    state = {}
if state.get('active'):
    raise SystemExit('Active drive: stop in the app before installing.')
CHECK
destination=/home/seo/d420_ws/scenario_lab
mkdir -p "$destination" /home/seo/d420_ws/team_app_backups
backup=$(mktemp -d /home/seo/d420_ws/team_app_backups/windows_update.XXXXXX)
tar -czf "$backup/app-before.tgz" --exclude=./runs --exclude=./backups --exclude=./models --exclude=./__pycache__ --exclude=./team_access/windows --exclude=./firmware/compiled_esp32s3 -C "$destination" .
tar -xzf /tmp/hongik-team-app.tar.gz -C "$destination"
bash "$destination/team_access/install_jetson.sh"
'@
    # A base64-encoded script avoids Windows PowerShell's native embedded-quote loss.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote.Replace("`r`n", "`n")))
    & $ssh.Source @options 'hongik-cafe' ('echo ' + $encoded + ' | base64 -d | bash')
    if ($LASTEXITCODE -ne 0) { throw 'Jetson 설치가 완료되지 않았습니다. 위의 오류를 확인하세요.' }
    Write-Host '자동 시작 설치 완료. 이후 로봇을 켜고 HONGIK CAFE 바로가기로 접속하세요.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Enter를 눌러 종료'
    exit 1
}
Read-Host 'Enter를 눌러 종료'
