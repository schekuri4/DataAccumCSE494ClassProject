param(
    [string]$PythonExe = "c:/Users/schek/OneDrive/Desktop/494 project/.venv/Scripts/python.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found at $PythonExe"
}

& $PythonExe -m pip install --upgrade pip uv
& "$PSScriptRoot\..\.venv\Scripts\uv.exe" pip install unsloth --torch-backend=auto

Write-Host "Unsloth installed into the workspace virtual environment."
Write-Host "Next:"
Write-Host "  $PythonExe scripts/train_unsloth_windows.py --max-steps 10"