param(
  [string]$Distro = "Ubuntu-24.04",
  [string]$InstallerWindowsPath = "",
  [string]$InstallRoot = "/opt/AMD/Vitis/2025.2",
  [switch]$SkipApt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Convert-ToWslPath {
  param([string]$WindowsPath)
  if (-not $WindowsPath) { return "" }
  if (-not (Test-Path -LiteralPath $WindowsPath)) {
    throw "Installer path not found: $WindowsPath"
  }
  $full = (Resolve-Path -LiteralPath $WindowsPath).Path
  $drive = $full.Substring(0,1).ToLowerInvariant()
  $rest = $full.Substring(2).Replace('\','/')
  return "/mnt/$drive$rest"
}

$repoWin = (Get-Location).Path
$repoWsl = "/mnt/{0}{1}" -f $repoWin.Substring(0,1).ToLowerInvariant(), $repoWin.Substring(2).Replace('\','/')
$installerWsl = Convert-ToWslPath -WindowsPath $InstallerWindowsPath

$args = @("$repoWsl/scripts/wsl_vitis_quick_setup.sh", "--install-root", $InstallRoot)
if ($installerWsl) {
  $args += @("--installer", $installerWsl)
}
if ($SkipApt) {
  $args += "--skip-apt"
}

$bashCmd = "chmod +x '$repoWsl/scripts/wsl_vitis_quick_setup.sh'; bash '$($args[0])'"
for ($i = 1; $i -lt $args.Count; $i++) {
  $bashCmd += " '$($args[$i])'"
}

Write-Output "Running on distro: $Distro"
Write-Output "WSL repo path: $repoWsl"
if ($installerWsl) { Write-Output "Installer path: $installerWsl" }

wsl -d $Distro -- bash -lc $bashCmd
