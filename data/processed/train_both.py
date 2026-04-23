import json, torch, os, gc
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer, BitsAndBytesConfig, DataCollatorForSeq2Seq, EarlyStoppingCallback
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import load_dataset
from huggingface_hub import HfApi

# Convert dataset once
with open('/home/ubuntu/aie_instruction_all.jsonl', 'r') as fin, open('/home/ubuntu/aie_alpaca.jsonl', 'w') as fout:
    for line in fin:
        try:
            obj = json.loads(line)
            fout.write(json.dumps({'instruction': obj['instruction'], 'input': obj.get('context', ''), 'output': obj['response']}) + '\n')
        except:
            pass
print('Dataset converted')

full_dataset = load_dataset('json', data_files='/home/ubuntu/aie_alpaca.jsonl', split='train')
split = full_dataset.train_test_split(test_size=0.1, seed=42)
train_dataset = split['train']
eval_dataset = split['test']
print(f'Train size: {len(train_dataset)}, Eval size: {len(eval_dataset)}')

def tokenize_dataset(tokenizer, dataset):
    def tokenize(example):
        prompt_text = "Below is an instruction that describes a task. Write a response that appropriately completes the request.\n\n### Instruction:\n" + example["instruction"] + "\n\n### Input:\n" + example["input"] + "\n\n### Response:\n"
        full_text = prompt_text + example['output']
        prompt_tokens = tokenizer(prompt_text, truncation=False, add_special_tokens=True)['input_ids']
        full_tokens = tokenizer(full_text, truncation=True, max_length=2048, add_special_tokens=True)
        labels = full_tokens['input_ids'].copy()
        for i in range(min(len(prompt_tokens), len(labels))):
            labels[i] = -100
        full_tokens['labels'] = labels
        return full_tokens
    return dataset.map(tokenize, remove_columns=dataset.column_names, num_proc=4)

def train_model(model_id, repo_name, batch_size):
    print(f'\n{"="*60}')
    print(f'TRAINING: {model_id}')
    print(f'{"="*60}\n')

    bnb_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)

    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(model_id, quantization_config=bnb_config, device_map='auto', trust_remote_code=True, dtype=torch.bfloat16)
    model = prepare_model_for_kbit_training(model)
    model.config.use_cache = False

    mem = torch.cuda.memory_allocated(0) / 1024**3
    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f'GPU: {mem:.1f} / {total:.1f} GB used')

    lora_config = LoraConfig(r=16, lora_alpha=32, target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'], lora_dropout=0.1, bias='none', task_type='CAUSAL_LM')
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    train_tokenized = tokenize_dataset(tokenizer, train_dataset)
    eval_tokenized = tokenize_dataset(tokenizer, eval_dataset)
    print(f'Train tokenized: {len(train_tokenized)}, Eval tokenized: {len(eval_tokenized)}')

    collator = DataCollatorForSeq2Seq(tokenizer, padding=True, pad_to_multiple_of=8, label_pad_token_id=-100)

    output_dir = f'/home/ubuntu/{repo_name}_output'
    args = TrainingArguments(
        output_dir=output_dir,
        num_train_epochs=1,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=max(1, 16 // batch_size),
        learning_rate=1e-4,
        bf16=True,
        tf32=True,
        logging_steps=10,
        save_steps=100,
        save_total_limit=3,
        warmup_steps=20,
        lr_scheduler_type='cosine',
        report_to='none',
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={'use_reentrant': False},
        optim='adamw_torch',
        weight_decay=0.01,
        dataloader_num_workers=4,
        eval_strategy='steps',
        eval_steps=100,
        load_best_model_at_end=True,
        metric_for_best_model='eval_loss',
        greater_is_better=False,
    )

    trainer = Trainer(model=model, args=args, train_dataset=train_tokenized, eval_dataset=eval_tokenized, data_collator=collator, callbacks=[EarlyStoppingCallback(early_stopping_patience=3)])
    trainer.train()
    print(f'{model_id} training complete!')

    adapter_path = f'{output_dir}/final_adapter'
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    print('Adapter saved locally')

    repo_id = f'schekur2/{repo_name}'
    api = HfApi()
    api.create_repo(repo_id=repo_id, exist_ok=True, private=True)
    api.upload_folder(folder_path=adapter_path, repo_id=repo_id, commit_message=f'{model_id} AIE QLoRA adapter - 11K dataset')
    print(f'Uploaded to https://huggingface.co/{repo_id}')

    # Clean up GPU memory before next model
    del model, trainer, train_tokenized, eval_tokenized
    gc.collect()
    torch.cuda.empty_cache()
    print('GPU memory cleared\n')

# Train both models back to back
train_model('Qwen/Qwen2.5-7B-Instruct', 'qwen2.5-7b-aie-qlora', batch_size=16)
train_model('Qwen/Qwen2.5-14B-Instruct', 'qwen2.5-14b-aie-qlora', batch_size=8)

print('\n' + '='*60)
print('ALL DONE. Both models trained and uploaded.')
print('='*60)
