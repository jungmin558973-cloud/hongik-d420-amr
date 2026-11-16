#requires -Version 5.1
param([string]$JetsonAddress = '', [int]$LocalPort = 0)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Connection.ps1')
if ($JetsonAddress -or $LocalPort) {
    $stateRoot = Join-Path $env:LOCALAPPDATA 'HongikCafe'
    $config = Get-HongikConfig $PSScriptRoot $stateRoot
    if (-not $JetsonAddress) { $JetsonAddress = [string]$config.jetson_address }
    if (-not $LocalPort) { $LocalPort = [int]$config.local_port }
    Save-HongikConfig $stateRoot $JetsonAddress $LocalPort
}
& (Join-Path $PSScriptRoot 'Launch-HongikCafe.ps1')
