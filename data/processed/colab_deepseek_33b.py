!pip install transformers peft bitsandbytes accelerate datasets huggingface_hub -q

import json
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer, BitsAndBytesConfig, DataCollatorForSeq2Seq, EarlyStoppingCallback
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import load_dataset
from huggingface_hub import HfApi

# Convert dataset
input_file = '/content/aie_instruction_all.jsonl'
output_file = '/content/aie_alpaca.jsonl'

with open(input_file, 'r') as fin, open(output_file, 'w') as fout:
    for line in fin:
        try:
            obj = json.loads(line)
            converted = {
                "instruction": obj["instruction"],
                "input": obj.get("context", ""),
                "output": obj["response"]
            }
            fout.write(json.dumps(converted) + '\n')
        except:
            pass
print("Dataset converted")

# Load and split
full_dataset = load_dataset("json", data_files="/content/aie_alpaca.jsonl", split="train")
split = full_dataset.train_test_split(test_size=0.1, seed=42)
train_dataset = split["train"]
eval_dataset = split["test"]
print(f"Train size: {len(train_dataset)}, Eval size: {len(eval_dataset)}")

# Load model
model_id = "deepseek-ai/deepseek-coder-33b-instruct"
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True
)

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    model_id,
    quantization_config=bnb_config,
    device_map="auto",
    trust_remote_code=True
)
model = prepare_model_for_kbit_training(model)
model.config.use_cache = False

# LoRA
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_dropout=0.1,
    bias="none",
    task_type="CAUSAL_LM"
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# Tokenize with response-only masking
def tokenize(example):
    prompt_text = f"""Below is an instruction that describes a task. Write a response that appropriately completes the request.

### Instruction:
{example['instruction']}

### Input:
{example['input']}

### Response:
"""
    full_text = prompt_text + example['output']

    prompt_tokens = tokenizer(prompt_text, truncation=False, add_special_tokens=True)["input_ids"]
    full_tokens = tokenizer(full_text, truncation=True, max_length=2048, add_special_tokens=True)

    labels = full_tokens["input_ids"].copy()
    for i in range(min(len(prompt_tokens), len(labels))):
        labels[i] = -100

    full_tokens["labels"] = labels
    return full_tokens

train_tokenized = train_dataset.map(tokenize, remove_columns=train_dataset.column_names, num_proc=4)
eval_tokenized = eval_dataset.map(tokenize, remove_columns=eval_dataset.column_names, num_proc=4)
print(f"Train tokenized: {len(train_tokenized)}, Eval tokenized: {len(eval_tokenized)}")

# Dynamic padding
collator = DataCollatorForSeq2Seq(tokenizer, padding=True, pad_to_multiple_of=8, label_pad_token_id=-100)

# Training
args = TrainingArguments(
    output_dir="/content/deepseek_output",
    num_train_epochs=1,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    learning_rate=1e-4,
    bf16=True,
    tf32=True,
    logging_steps=10,
    save_steps=100,
    save_total_limit=3,
    warmup_steps=20,
    lr_scheduler_type="cosine",
    report_to="none",
    gradient_checkpointing=True,
    gradient_checkpointing_kwargs={"use_reentrant": False},
    optim="paged_adamw_8bit",
    weight_decay=0.01,
    eval_strategy="steps",
    eval_steps=100,
    load_best_model_at_end=True,
    metric_for_best_model="eval_loss",
    greater_is_better=False,
)

trainer = Trainer(
    model=model,
    args=args,
    train_dataset=train_tokenized,
    eval_dataset=eval_tokenized,
    data_collator=collator,
    callbacks=[EarlyStoppingCallback(early_stopping_patience=3)],
)

trainer.train()
print("Training complete!")

# Save adapter
model.save_pretrained("/content/deepseek_output/final_adapter")
tokenizer.save_pretrained("/content/deepseek_output/final_adapter")
print("Adapter saved locally")

# Upload to HF Hub
from huggingface_hub import login
# NOTE: set HF_TOKEN in your Colab secrets / env; never commit a literal token.
import os
login(token=os.environ["HF_TOKEN"])
repo_id = "schekur2/deepseek-coder-33b-aie-qlora"
api = HfApi()
api.create_repo(repo_id=repo_id, exist_ok=True, private=True)
api.upload_folder(folder_path="/content/deepseek_output/final_adapter", repo_id=repo_id, commit_message="DeepSeek Coder 33B AIE QLoRA adapter - 11K dataset")
print(f"Uploaded to https://huggingface.co/{repo_id}")
print("Done!")
