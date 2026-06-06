# Bug Famalies

This folder now supports local Bedrock-backed generation of AIE bug-family definitions and per-family Python mutator code.

Files:

- `secrets.json`: local Bedrock token and model configuration. This file is git-ignored.
- `generate_bug_families_bedrock.py`: calls the Bedrock Converse API and asks the configured Claude model to generate bug families by AIE topic area, then generate one Python mutator module per family.
- `generated/`: output folder for generated JSONL and summary files.
- `bug_family_catalog.py`: local structured catalog work from the earlier step; you can keep it as a seed/reference, but the Bedrock script does not require it.

Setup:

1. Open `bug famalies/secrets.json`.
2. Paste your Bedrock bearer token into `aws_bearer_token_bedrock`.
3. Replace `model_id` with the exact Bedrock model ID you want to use for Claude Opus 4.6 in your account.

Usage:

```powershell
python "bug famalies/generate_bug_families_bedrock.py" --probe
python "bug famalies/generate_bug_families_bedrock.py"
``` 