# Native Windows Unsloth Setup

This path replaces the WSL-first Axolotl flow with a native Windows Unsloth experiment.

## Model choice

The current native Windows target is:

- `Qwen/Qwen2.5-Coder-7B-Instruct`

That is the strongest practical coding-oriented target for your RTX 3080 10 GB. The training script loads it in 4-bit and keeps the setup GPU-only instead of relying on CPU offload.

## Files

- `scripts/setup_unsloth_windows.ps1`: installs Unsloth into the repo virtual environment.
- `scripts/train_unsloth_windows.py`: formats the existing instruction dataset and launches SFT.

## Install

From PowerShell at the repo root:

```powershell
.\scripts\setup_unsloth_windows.ps1
```

## Smoke test

Run a short fit test first:

```powershell
& "c:/Users/schek/OneDrive/Desktop/494 project/.venv/Scripts/python.exe" scripts/train_unsloth_windows.py --max-steps 10
```

If it survives the first few optimizer steps, then increase the run length.

## Notes for your GPU

- GPU: RTX 3080 10 GB
- RAM: 64 GB
- This 7B setup is the practical local path for your machine.
- Default context is `1024` tokens.
- If you OOM, lower `--max-seq-length` from `1024` to `768` or `512`.
- If it trains cleanly, increase `--max-steps` first before changing model size.
