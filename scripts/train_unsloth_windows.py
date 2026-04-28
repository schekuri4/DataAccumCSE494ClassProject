from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
import unsloth
from datasets import Dataset
from unsloth import FastLanguageModel
from trl import SFTConfig, SFTTrainer


ROOT = Path(__file__).resolve().parents[1]
TRAIN_PATH = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_train.jsonl"
VALIDATION_PATH = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_validation.jsonl"
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"
SYSTEM_PROMPT = (
    "You are an expert AMD/Xilinx Versal AIE and HLS coding assistant. "
    "Give precise, technically grounded code help with minimal fluff."
)


def build_prompt(instruction: str, context: str, response: str, tokenizer) -> str:
    user_content = instruction.strip()
    if context and context.strip():
        user_content = f"{user_content}\n\nContext:\n{context.strip()}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": response.strip()},
    ]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)


def format_dataset(path: Path, tokenizer):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append(
                {
                    "text": build_prompt(
                        row["instruction"],
                        row.get("context", ""),
                        row["response"],
                        tokenizer,
                    )
                }
            )

    return Dataset.from_list(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=16)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--output-dir", default=str(ROOT / "outputs" / "unsloth-qwen25-coder-7b"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"Loading model: {args.model_name}", flush=True)

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this training script.")

    print(
        f"GPU: {torch.cuda.get_device_name(0)} with {torch.cuda.get_device_properties(0).total_memory // (1024**3)} GiB VRAM",
        flush=True,
    )

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.model_name,
        max_seq_length=args.max_seq_length,
        dtype=None,
        load_in_4bit=True,
    )
    print("Model and tokenizer loaded.", flush=True)

    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        lora_alpha=args.lora_r * 2,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
        max_seq_length=args.max_seq_length,
    )
    print("LoRA adapters attached.", flush=True)

    train_dataset = format_dataset(TRAIN_PATH, tokenizer)
    eval_dataset = format_dataset(VALIDATION_PATH, tokenizer)
    print(
        f"Datasets prepared. Train rows={len(train_dataset)}, validation rows={len(eval_dataset)}",
        flush=True,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        dataset_text_field="text",
        args=SFTConfig(
            output_dir=args.output_dir,
            max_seq_length=args.max_seq_length,
            per_device_train_batch_size=args.batch_size,
            per_device_eval_batch_size=1,
            gradient_accumulation_steps=args.grad_accum,
            warmup_steps=5,
            max_steps=args.max_steps,
            num_train_epochs=args.epochs,
            learning_rate=args.learning_rate,
            logging_steps=1,
            eval_strategy="steps",
            eval_steps=max(5, args.max_steps),
            save_strategy="steps",
            save_steps=max(5, args.max_steps),
            save_total_limit=2,
            optim="adamw_8bit",
            seed=3407,
            dataset_num_proc=1,
            report_to=[],
        ),
    )

    print("Starting trainer.train()", flush=True)
    trainer.train()
    print("Training finished. Saving adapter.", flush=True)
    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Saved outputs to {args.output_dir}", flush=True)


if __name__ == "__main__":
    main()