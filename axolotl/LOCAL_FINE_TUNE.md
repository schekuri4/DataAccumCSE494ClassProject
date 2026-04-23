# Local Axolotl QLoRA Setup

This repo now includes a local QLoRA path for the larger instruction dataset in `data/processed/aie_instruction_train.jsonl`.

## What to use

- `axolotl/aie_instruction_qlora_7b.yml`: 10 GB VRAM tuned 7B local profile.
- `axolotl/aie_instruction_qlora_14b.yml`: 10 GB VRAM offloaded 14B fallback profile.
- `scripts/setup_axolotl_wsl.sh`: creates a WSL2 Python environment and installs Axolotl.
- `scripts/wsl_axolotl.ps1`: runs Axolotl commands from Windows PowerShell through WSL.

## Platform note

Axolotl recommends WSL2 or Docker on Windows. Native Windows installs are possible in some cases, but WSL2 is the practical path for QLoRA fine-tuning.

Minimum practical hardware:

- 7B QLoRA: practical on your RTX 3080 10 GB with reduced context length.
- 14B QLoRA: not a good fit for 10 GB VRAM; the included profile relies on CPU and layer offloading and will be much slower.

If you hit out-of-memory errors, reduce `sequence_len` first, then increase `gradient_accumulation_steps` to preserve effective batch size.

For your machine specifically:

- Recommended: use the 7B profile.
- Experimental only: use the 14B profile if you are willing to trade a lot of speed for fitting the model.

## One-time setup

1. Install WSL2 with an Ubuntu distribution.
2. Make sure the NVIDIA WSL driver stack is working and that `nvidia-smi` succeeds inside WSL.
3. Accept the Hugging Face license for the base model you plan to fine-tune.
4. From Windows PowerShell at the repo root, run:

```powershell
.\scripts\wsl_axolotl.ps1 -NoVenv -Command "./scripts/setup_axolotl_wsl.sh"
```

5. Authenticate to Hugging Face inside the WSL environment:

```powershell
.\scripts\wsl_axolotl.ps1 -Command "hf auth login"
```

## Preprocess and train

Inspect dataset preprocessing for the 7B profile:

```powershell
.\scripts\wsl_axolotl.ps1 -Command "axolotl preprocess axolotl/aie_instruction_qlora_7b.yml --debug"
```

Train the 7B profile:

```powershell
.\scripts\wsl_axolotl.ps1 -Command "axolotl train axolotl/aie_instruction_qlora_7b.yml"
```

Train the 14B profile:

```powershell
.\scripts\wsl_axolotl.ps1 -Command "axolotl train axolotl/aie_instruction_qlora_14b.yml"
```

On an RTX 3080 10 GB, start with the 7B profile. The 14B profile is mainly here so you can experiment with offloaded training, not because it is the efficient option.

## Merge the adapter

After training finishes, merge the adapter into the base model if you want a standalone checkpoint:

```powershell
.\scripts\wsl_axolotl.ps1 -Command "axolotl merge-lora axolotl/aie_instruction_qlora_7b.yml"
```

Repeat with the 14B config if you trained that profile.

## Changing the base model

The included profiles use Qwen 2.5 Instruct because that family has both 7B and 14B variants. To switch models, edit `base_model` in the YAML file and keep the size-aligned profile.

Reasonable swaps:

- 7B profile: another 7B to 8B instruct model.
- 14B profile: another 13B to 14B instruct model.

When changing families, verify the tokenizer chat or prompt format still matches your target model.

## Common adjustments

- Less VRAM: reduce the 7B `sequence_len` from `1024` to `768` or `512`.
- More VRAM: raise the 7B `sequence_len` gradually toward `1536` or `2048`.
- If 14B still OOMs: lower its `sequence_len` below `512`, or stop and use the 7B profile instead.
- Slower but safer install: set `INSTALL_FLASH_ATTN=0` when running the setup script.

Example with Flash Attention disabled:

```powershell
.\scripts\wsl_axolotl.ps1 -NoVenv -Command "INSTALL_FLASH_ATTN=0 ./scripts/setup_axolotl_wsl.sh"
```
