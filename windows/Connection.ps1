# Shared by the Windows launcher. Compatible with Windows PowerShell 5.1.
function Get-HongikConfig {
    param([string]$PackageRoot, [string]$StateRoot)
    $values = @{ jetson_address = '192.168.0.47'; local_port = 18765 }
    foreach ($path in @((Join-Path $PackageRoot 'connection.json'), (Join-Path $StateRoot 'connection.json'))) {
        if (Test-Path -LiteralPath $path) {
            $saved = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json
            foreach ($key in @('jetson_address', 'local_port')) {
                if ($null -ne $saved.$key) { $values[$key] = $saved.$key }
            }
        }
    }
    Assert-HongikAddress ([string]$values.jetson_address)
    Assert-HongikPort ([int]$values.local_port)
    return [pscustomobject]$values
}

function Assert-HongikAddress {
    param([string]$Address)
    if ($Address -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$') {
        throw 'Jetson IP 또는 호스트 이름만 입력하세요. 예: 192.168.0.47 (ssh, seo@, 공백 제외)'
    }
}

function Assert-HongikPort {
    param([int]$Port)
    if ($Port -lt 1024 -or $Port -gt 65535) { throw '접속 포트는 1024~65535 범위여야 합니다.' }
}

function Save-HongikConfig {
    param([string]$StateRoot, [string]$Address, [int]$Port)
    Assert-HongikAddress $Address
    Assert-HongikPort $Port
    [void][System.IO.Directory]::CreateDirectory($StateRoot)
    $json = @{ jetson_address = $Address; local_port = $Port } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $StateRoot 'connection.json'), $json, (New-Object System.Text.UTF8Encoding($false)))
}

function New-HongikSshConfig {
    param([string]$PackageRoot, [string]$StateRoot, [string]$Address, [int]$Port)
    Assert-HongikAddress $Address
    Assert-HongikPort $Port
    $key = Join-Path $PackageRoot 'jetson_known_hosts'
    if (-not (Test-Path -LiteralPath $key)) { throw 'jetson_known_hosts 파일이 없습니다. ZIP을 모두 압축 해제하세요.' }
    if ($key -match '[\r\n"]') { throw '배포 폴더 경로에 허용되지 않는 문자가 있습니다.' }
    [void][System.IO.Directory]::CreateDirectory($StateRoot)
    $path = Join-Path $StateRoot 'ssh_config'
    $known = $key.Replace('\', '/')
    $text = @"
Host hongik-cafe
    HostName $Address
    User seo
    Port 22
    HostKeyAlias hongik-jetson-team
    UserKnownHostsFile "$known"
    StrictHostKeyChecking yes
    ExitOnForwardFailure yes
    ServerAliveInterval 3
    ServerAliveCountMax 2
    ConnectTimeout 5
    ConnectionAttempts 36
    LogLevel ERROR
    LocalForward 127.0.0.1:$Port 127.0.0.1:8765
"@
    [System.IO.File]::WriteAllText($path, $text + "`n", (New-Object System.Text.UTF8Encoding($false)))
    return $path
}

function Get-HongikSession {
    param([int]$Port)
    Assert-HongikPort $Port
    $response = $null
    $reader = $null
    try {
        $request = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/api/session")
        $request.Proxy = $null
        $request.Timeout = 450
        $request.ReadWriteTimeout = 450
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $session = $reader.ReadToEnd() | ConvertFrom-Json
        if (($session.allow_real -is [bool]) -and $session.allow_real -and $session.token) { return $session }
    } catch { return $null }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
    return $null
}

function Send-HongikStop {
    param([int]$Port)
    # Only stop is exposed by this launcher. Connection/reconnection never arms a drive.
    $session = Get-HongikSession $Port
    if (-not $session) { return $false }
    $response = $null
    $reader = $null
    try {
        $request = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/api/stop")
        $request.Proxy = $null
        $request.Method = 'POST'
        $request.ContentType = 'application/json'
        $request.Headers.Add('X-Hongik-Control', [string]$session.token)
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $data = [System.Text.Encoding]::UTF8.GetBytes('{}')
        $request.ContentLength = $data.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($data, 0, $data.Length) } finally { $stream.Dispose() }
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $result = $reader.ReadToEnd() | ConvertFrom-Json
        return (($result.ok -is [bool]) -and $result.ok)
    } catch { return $false }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}
