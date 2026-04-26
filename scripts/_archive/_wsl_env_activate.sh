#!/usr/bin/env bash
set -eo pipefail
# settings64.sh references PYTHONPATH which may be unset; pre-initialise it
export PYTHONPATH="${PYTHONPATH:-}"

# Write Vitis env hook (install was placed at /vitis/2025.2)
cat > "$HOME/.vitis_aie_env.sh" << 'EOF'
export PYTHONPATH="${PYTHONPATH:-}"
export MATLABPATH="${MATLABPATH:-}"
set +u
source /vitis/2025.2/Vitis/settings64.sh
set -u
export PLATFORM_REPO_PATHS=/vitis/2025.2/Vitis/base_platforms
export XILINXD_LICENSE_FILE=/home/schek/.Xilinx/Xilinx.lic
# User-local locale to satisfy en_US.UTF-8 required by aietools
export LOCPATH="${HOME}/.local/locale"
# Ensure locale dir exists with en_US.UTF-8
mkdir -p "${HOME}/.local/locale"
localedef -i en_US -c -f UTF-8 "${HOME}/.local/locale/en_US.UTF-8" 2>/dev/null || true
EOF

# Copy license file from Windows Downloads into WSL
mkdir -p "$HOME/.Xilinx"
cp '/mnt/c/Users/schek/Downloads/Xilinx (2).lic' "$HOME/.Xilinx/Xilinx.lic"
echo "[activate] License copied to $HOME/.Xilinx/Xilinx.lic"

# Add env hook to bashrc if not already present
if ! grep -q '.vitis_aie_env.sh' "$HOME/.bashrc"; then
  echo 'source ~/.vitis_aie_env.sh' >> "$HOME/.bashrc"
  echo "[activate] Added source hook to ~/.bashrc"
fi

# Source and verify
# shellcheck disable=SC1090
source "$HOME/.vitis_aie_env.sh"

echo "[activate] --- tool check ---"
for t in vitis v++ aiecompiler xchesscc; do
  p=$(command -v "$t" 2>/dev/null || true)
  if [[ -n "$p" ]]; then
    echo "FOUND $t: $p"
  else
    echo "MISSING $t"
  fi
done
echo "[activate] XILINXD_LICENSE_FILE=$XILINXD_LICENSE_FILE"
echo "[activate] done."
