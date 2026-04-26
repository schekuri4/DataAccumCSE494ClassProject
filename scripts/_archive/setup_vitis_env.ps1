param(
  [string]$VitisRoot = "",
  [switch]$SkipToolChecks
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-VitisRoot {
  param([string]$Preferred)

  $candidates = @()
  if ($Preferred) { $candidates += $Preferred }
  if ($env:XILINX_VITIS) { $candidates += $env:XILINX_VITIS }
  $candidates += @(
    'C:\AMDDesignTools\2025.2\Vitis',
    'C:\AMDDesignTools\2025.1\Vitis',
    'C:\AMDDesignTools\2024.2\Vitis',
    'C:\Xilinx\2025.2\Vitis',
    'C:\Xilinx\2024.2\Vitis',
    'D:\fpga\2025.2\Vitis',
    'D:\fpga\2025.1\Vitis'
  )

  foreach ($cand in $candidates) {
    if (-not $cand) { continue }
    if (Test-Path -LiteralPath (Join-Path $cand 'settings64.bat')) {
      return (Resolve-Path -LiteralPath $cand).Path
    }
  }
  return $null
}

function Import-EnvFromSettings64 {
  param([string]$Root)

  $settingsBat = Join-Path $Root 'settings64.bat'
  if (-not (Test-Path -LiteralPath $settingsBat)) {
    throw "settings64.bat not found at: $settingsBat"
  }

  # Run settings64.bat in cmd, then dump full environment and import into current PowerShell process.
  $cmd = 'call "{0}" >nul 2>&1 && set' -f $settingsBat
  $lines = cmd.exe /d /s /c $cmd
  if (-not $lines) {
    throw "Failed to import environment from $settingsBat"
  }

  foreach ($line in $lines) {
    if ($line -notmatch '=') { continue }
    $idx = $line.IndexOf('=')
    if ($idx -lt 1) { continue }
    $name = $line.Substring(0, $idx)
    $value = $line.Substring($idx + 1)
    Set-Item -Path ("Env:{0}" -f $name) -Value $value
  }
}

$resolved = Resolve-VitisRoot -Preferred $VitisRoot
if (-not $resolved) {
  throw "Could not find a Vitis installation with settings64.bat. Pass -VitisRoot explicitly."
}

Import-EnvFromSettings64 -Root $resolved

if (-not $env:PLATFORM_REPO_PATHS -and $env:XILINX_VITIS) {
  $defaultRepo = Join-Path $env:XILINX_VITIS 'base_platforms'
  if (Test-Path -LiteralPath $defaultRepo) {
    $env:PLATFORM_REPO_PATHS = $defaultRepo
  }
}

function Show-OrUnset {
  param([string]$Value)
  if ($Value) { return $Value }
  return '<unset>'
}

Write-Output ("Vitis root: {0}" -f $resolved)
Write-Output ("XILINX_VITIS: {0}" -f (Show-OrUnset $env:XILINX_VITIS))
Write-Output ("XILINX_HLS: {0}" -f (Show-OrUnset $env:XILINX_HLS))
Write-Output ("PLATFORM_REPO_PATHS: {0}" -f (Show-OrUnset $env:PLATFORM_REPO_PATHS))
Write-Output ("License set: {0}" -f ($(if ($env:XILINXD_LICENSE_FILE -or $env:LM_LICENSE_FILE) { 'yes' } else { 'no' })))

if (-not $SkipToolChecks) {
  $tools = @('vitis', 'v++', 'aiecompiler', 'xchesscc')
  foreach ($t in $tools) {
    $cmd = Get-Command $t -ErrorAction SilentlyContinue
    if ($cmd) {
      Write-Output ("FOUND {0}: {1}" -f $t, $cmd.Source)
    } else {
      Write-Output ("MISSING {0}" -f $t)
    }
  }
}

Write-Output 'Environment loaded into this PowerShell session.'
