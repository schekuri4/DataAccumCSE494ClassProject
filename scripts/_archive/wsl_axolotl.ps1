param(
    [Parameter(Mandatory = $true)]
    [string]$Command,

    [switch]$NoVenv,

    [string]$VenvDir = ".wsl-venv"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRootWsl = (wsl.exe wslpath -a $repoRoot).Trim()

if (-not $repoRootWsl) {
    throw "Unable to resolve the workspace path inside WSL."
}

$segments = @(
    'set -euo pipefail',
    "cd '$repoRootWsl'"
)

if (-not $NoVenv) {
    $segments += "source '$VenvDir/bin/activate'"
}

$segments += $Command

wsl.exe bash -lc ($segments -join '; ')