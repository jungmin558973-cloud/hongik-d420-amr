#requires -Version 5.1
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    $destination = Join-Path $env:LOCALAPPDATA 'HongikCafe\app'
    if ([System.IO.Path]::GetFullPath($destination) -ne [System.IO.Path]::GetFullPath($PSScriptRoot)) {
        [void][System.IO.Directory]::CreateDirectory($destination)
        foreach ($item in Get-ChildItem -LiteralPath $PSScriptRoot -File) {
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $destination $item.Name) -Force
        }
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'HONGIK CAFE.lnk'))
    $shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
    $shortcut.Arguments = '-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $destination 'Launch-HongikCafe.ps1') + '"'
    $shortcut.WorkingDirectory = $destination
    $shortcut.Description = 'Jetson 로봇 연결 · HONGIK CAFE'
    $shortcut.Save()
    [void][System.Windows.Forms.MessageBox]::Show('바탕화면에 HONGIK CAFE를 만들었습니다. 로봇 전원을 켠 뒤 바로가기를 실행하세요.', '설치 완료')
} catch {
    [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '설치 오류')
    exit 1
}
