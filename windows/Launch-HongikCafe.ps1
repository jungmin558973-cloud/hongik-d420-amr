#requires -Version 5.1
param([switch]$NoAutoConnect)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
. (Join-Path $PSScriptRoot 'Connection.ps1')
$stateRoot = Join-Path $env:LOCALAPPDATA 'HongikCafe'
$mutex = New-Object System.Threading.Mutex($false, 'Local\HongikCafeLauncher')
$ownsMutex = $false
try {
    try { $ownsMutex = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) {
        [void][System.Windows.Forms.MessageBox]::Show('이미 실행 중인 HONGIK CAFE 연결 창을 사용하세요.', 'HONGIK CAFE')
        exit
    }
    $config = Get-HongikConfig $PSScriptRoot $stateRoot
    $script:tunnel = $null
    $script:connected = $false
    $script:everConnected = $false
    $script:requested = $false
    $script:retryAt = [datetime]::MaxValue
    $script:deadline = [datetime]::MaxValue
    $script:port = [int]$config.local_port
    $script:opened = $false

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'HONGIK CAFE | Windows 로봇 연결'
    $form.ClientSize = New-Object System.Drawing.Size(610, 470)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.BackColor = [System.Drawing.Color]::FromArgb(246, 247, 241)
    $form.Font = New-Object System.Drawing.Font('Malgun Gothic', 10)

    $title = New-Object System.Windows.Forms.Label
    $title.Text = 'HONGIK CAFE'
    $title.Font = New-Object System.Drawing.Font('Malgun Gothic', 23, [System.Drawing.FontStyle]::Bold)
    $title.SetBounds(26, 21, 550, 48)
    $title.ForeColor = [System.Drawing.Color]::FromArgb(27, 76, 63)
    $form.Controls.Add($title)
    $intro = New-Object System.Windows.Forms.Label
    $intro.Text = '로봇을 켜고 같은 K221 Wi-Fi에 연결하세요.'
    $intro.SetBounds(29, 77, 550, 26)
    $form.Controls.Add($intro)
    $addressLabel = New-Object System.Windows.Forms.Label
    $addressLabel.Text = 'Jetson 주소'
    $addressLabel.SetBounds(29, 119, 110, 26)
    $form.Controls.Add($addressLabel)
    $address = New-Object System.Windows.Forms.TextBox
    $address.Text = [string]$config.jetson_address
    $address.SetBounds(144, 115, 285, 30)
    $form.Controls.Add($address)
    $connect = New-Object System.Windows.Forms.Button
    $connect.Text = '연결하기'
    $connect.SetBounds(443, 113, 136, 35)
    $form.Controls.Add($connect)
    $auto = New-Object System.Windows.Forms.CheckBox
    $auto.Text = '연결 후 끊어지면 다시 연결 (주행은 자동 시작하지 않음)'
    $auto.Checked = $true
    $auto.SetBounds(29, 161, 550, 30)
    $form.Controls.Add($auto)
    $status = New-Object System.Windows.Forms.Label
    $status.Text = '연결 대기'
    $status.Font = New-Object System.Drawing.Font('Malgun Gothic', 12, [System.Drawing.FontStyle]::Bold)
    $status.SetBounds(29, 208, 550, 48)
    $form.Controls.Add($status)
    $detail = New-Object System.Windows.Forms.Label
    $detail.Text = '처음 연결할 때 SSH 창에 seo 계정 암호를 입력합니다.'
    $detail.SetBounds(29, 258, 550, 44)
    $form.Controls.Add($detail)
    $drive = New-Object System.Windows.Forms.Button
    $drive.Text = '주행 · 트레이 앱 열기'
    $drive.Enabled = $false
    $drive.SetBounds(29, 314, 268, 43)
    $form.Controls.Add($drive)
    $map = New-Object System.Windows.Forms.Button
    $map.Text = '지도 · 저장 지점 열기'
    $map.Enabled = $false
    $map.SetBounds(310, 314, 268, 43)
    $form.Controls.Add($map)
    $stop = New-Object System.Windows.Forms.Button
    $stop.Text = '앱 정지 요청'
    $stop.Enabled = $false
    $stop.SetBounds(29, 370, 175, 38)
    $form.Controls.Add($stop)
    $disconnect = New-Object System.Windows.Forms.Button
    $disconnect.Text = '접속 종료'
    $disconnect.SetBounds(218, 370, 175, 38)
    $form.Controls.Add($disconnect)
    $help = New-Object System.Windows.Forms.Button
    $help.Text = '사용 방법'
    $help.SetBounds(407, 370, 171, 38)
    $form.Controls.Add($help)
    $footer = New-Object System.Windows.Forms.Label
    $footer.Text = '앱에서 R → WASD/방향키 · Space/Esc 정지 · 한 명씩 조종'
    $footer.SetBounds(29, 428, 555, 30)
    $form.Controls.Add($footer)

    function Write-ConnectionLog([string]$Message) {
        [void][System.IO.Directory]::CreateDirectory($stateRoot)
        Add-Content -LiteralPath (Join-Path $stateRoot 'launcher.log') -Encoding UTF8 -Value ('{0:o} {1}' -f (Get-Date), $Message)
    }
    function Set-ConnectedControls([bool]$Ready) {
        $drive.Enabled = $Ready; $map.Enabled = $Ready; $stop.Enabled = $Ready
        $address.Enabled = (-not $script:requested)
        $connect.Enabled = (-not $script:requested)
    }
    function Close-Tunnel {
        if ($script:tunnel) {
            $script:tunnel.Refresh()
            if (-not $script:tunnel.HasExited) { Stop-Process -Id $script:tunnel.Id -ErrorAction SilentlyContinue }
            $script:tunnel.Dispose(); $script:tunnel = $null
        }
        $script:connected = $false
    }
    function Begin-Connection {
        try {
            Close-Tunnel
            $ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
            if (-not $ssh) {
                Start-Process 'ms-settings:optionalfeatures'
                throw 'OpenSSH 클라이언트를 Windows 선택적 기능에서 설치한 뒤 다시 실행하세요.'
            }
            $target = $address.Text.Trim()
            Assert-HongikAddress $target
            Save-HongikConfig $stateRoot $target $script:port
            $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $script:port)
            try { $listener.Start() } catch { throw '접속 포트가 이미 사용 중입니다. 기존 HONGIK 접속 창을 닫아 주세요.' } finally { $listener.Stop() }
            $sshConfig = New-HongikSshConfig $PSScriptRoot $stateRoot $target $script:port
            if ($sshConfig -match '[\r\n"]') { throw '접속 설정 경로를 확인해 주세요.' }
            # One quoted config path avoids nested -o quoting and paths with spaces.
            $arguments = '-F "' + $sshConfig + '" -N hongik-cafe'
            $script:tunnel = Start-Process -FilePath $ssh.Source -ArgumentList $arguments -WindowStyle Normal -PassThru
            $script:requested = $true
            $script:retryAt = [datetime]::MaxValue
            $script:deadline = (Get-Date).AddSeconds(180)
            $status.Text = '로봇과 앱에 연결 중...'
            $detail.Text = '별도 SSH 창에 seo 암호를 입력하세요. 부팅 중이면 최대 3분 기다립니다.'
            Set-ConnectedControls $false
            Write-ConnectionLog ('Connecting to ' + $target)
        } catch {
            Close-Tunnel
            $script:requested = $false
            $script:retryAt = [datetime]::MaxValue
            $status.Text = '연결을 시작하지 못했습니다.'
            $detail.Text = $_.Exception.Message
            Set-ConnectedControls $false
        }
    }
    $connect.Add_Click({ Begin-Connection })
    $drive.Add_Click({ Start-Process "http://127.0.0.1:$($script:port)/?drive=teleop&mode=real" })
    $map.Add_Click({ Start-Process "http://127.0.0.1:$($script:port)/navigation" })
    $help.Add_Click({ Start-Process (Join-Path $PSScriptRoot 'START_HERE.html') })
    $stop.Add_Click({
        if (Send-HongikStop $script:port) { $detail.Text = '정지 요청을 전송했습니다. 차체 상태는 앱과 현장에서 확인하세요.' }
        else { $detail.Text = '정지 요청을 전달하지 못했습니다. 차체를 직접 확인하세요.' }
    })
    function Disconnect-Requested {
        $script:requested = $false
        $script:retryAt = [datetime]::MaxValue
        if ($script:tunnel) {
            $stopAccepted = Send-HongikStop $script:port
            if (-not $stopAccepted -and $script:everConnected) {
                [void][System.Windows.Forms.MessageBox]::Show('정지 요청을 전달하지 못했습니다. 차체를 직접 확인하세요.', '접속 종료')
            }
        }
        Close-Tunnel
        $script:opened = $false
        $status.Text = '접속 종료'
        $detail.Text = '다시 사용하려면 연결하기를 누르세요.'
        Set-ConnectedControls $false
    }
    $disconnect.Add_Click({ Disconnect-Requested })
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 1000
    $timer.Add_Tick({
        try {
            if ($script:requested -and $script:tunnel) {
                $script:tunnel.Refresh()
                if ($script:tunnel.HasExited -or (Get-Date) -gt $script:deadline) {
                    Close-Tunnel
                    $script:requested = $false
                    $status.Text = '연결이 종료됐습니다.'
                    $detail.Text = '전원 · K221 · 주소 · SSH 창의 암호/호스트 키 오류를 확인하세요.'
                    Set-ConnectedControls $false
                    if ($script:everConnected -and $auto.Checked) { $script:retryAt = (Get-Date).AddSeconds(5) }
                } else {
                    $session = Get-HongikSession $script:port
                    if ($session) {
                        $script:deadline = [datetime]::MaxValue
                        $script:connected = $true
                        $script:everConnected = $true
                        $status.Text = '앱 연결됨'
                        $detail.Text = '센서와 차체 준비 상태는 앱에서 확인하세요. 조종은 R로 직접 켭니다.'
                        Set-ConnectedControls $true
                        if (-not $script:opened) { Start-Process "http://127.0.0.1:$($script:port)/?drive=teleop&mode=real"; $script:opened = $true }
                    } else {
                        $script:connected = $false
                        Set-ConnectedControls $false
                        $status.Text = '앱 응답을 기다리는 중...'
                        $detail.Text = 'SSH 창을 확인하세요. 연결 복구만 수행하며 주행은 자동 시작하지 않습니다.'
                    }
                }
            } elseif ($auto.Checked -and (Get-Date) -ge $script:retryAt) { Begin-Connection }
        } catch {
            $status.Text = '연결 상태 확인 오류'
            $detail.Text = $_.Exception.Message
        }
    })
    $form.Add_Shown({ $timer.Start(); if (-not $NoAutoConnect) { Begin-Connection } })
    $form.Add_FormClosing({ $timer.Stop(); Disconnect-Requested })
    [void]$form.ShowDialog()
} catch {
    [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'HONGIK CAFE 실행 오류')
} finally {
    if ($script:tunnel) { Close-Tunnel }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
