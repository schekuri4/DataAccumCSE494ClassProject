#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${AXOLOTL_VENV_DIR:-.wsl-venv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
TORCH_VERSION="${TORCH_VERSION:-2.10.0}"
UV_TORCH_BACKEND="${UV_TORCH_BACKEND:-cu128}"
INSTALL_FLASH_ATTN="${INSTALL_FLASH_ATTN:-1}"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

export UV_TORCH_BACKEND

cd "$ROOT_DIR"

if [ ! -d "$VENV_DIR" ]; then
  uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

uv pip install -U packaging setuptools wheel ninja
uv pip install "torch==${TORCH_VERSION}" torchvision

if [ "$INSTALL_FLASH_ATTN" = "1" ]; then
  uv pip install --no-build-isolation "axolotl[deepspeed,flash-attn]"
else
  uv pip install --no-build-isolation "axolotl[deepspeed]"
fi

cat <<EOF
Axolotl environment is ready in $ROOT_DIR/$VENV_DIR

Next commands:
  source $VENV_DIR/bin/activate
  hf auth login
  axolotl preprocess axolotl/aie_instruction_qlora_7b.yml --debug
  axolotl train axolotl/aie_instruction_qlora_7b.yml
EOF