#!/usr/bin/env bash
# run_validate_wsl.sh
# Run validate_aie_compile.py from inside WSL Ubuntu where the AIE tools live.
# Usage (from Windows PowerShell):
#   wsl -d Ubuntu-24.04 -- bash "/mnt/c/Users/schek/OneDrive/Desktop/494 project/scripts/run_validate_wsl.sh" [args...]
#
# Or directly in WSL:
#   bash scripts/run_validate_wsl.sh --input data/processed/aie_instruction_validation.jsonl --out data/processed/compile_results/validation.jsonl --limit 20

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Activate Vitis environment
# settings64.sh uses variables that may be unbound; temporarily allow unset vars
export PYTHONPATH="${PYTHONPATH:-}"
export MATLABPATH="${MATLABPATH:-}"
set +u
# shellcheck disable=SC1090
source /vitis/2025.2/Vitis/settings64.sh
set -u
export PLATFORM_REPO_PATHS=/vitis/2025.2/Vitis/base_platforms
export XILINXD_LICENSE_FILE="${HOME}/.Xilinx/Xilinx.lic"
# User-local locale to satisfy en_US.UTF-8 required by xchesscc
export LOCPATH="${HOME}/.local/locale"
mkdir -p "${HOME}/.local/locale"
localedef -i en_US -c -f UTF-8 "${HOME}/.local/locale/en_US.UTF-8" 2>/dev/null || true

# Show tool versions for diagnostics
echo "[wsl-validate] xchesscc:    $(command -v xchesscc || echo MISSING)"
echo "[wsl-validate] aiecompiler: $(command -v aiecompiler || echo MISSING)"
echo "[wsl-validate] license:     ${XILINXD_LICENSE_FILE}"
echo "[wsl-validate] project:     ${PROJECT_ROOT}"
echo ""

# Use system python3 (stdlib only needed)
PYTHON=$(command -v python3)

cd "$PROJECT_ROOT"
exec "$PYTHON" scripts/validate_aie_compile.py "$@"
