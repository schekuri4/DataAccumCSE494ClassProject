# Raw Example Format

Store curated source examples as one JSON file per task under `data/raw/examples/`.

The builder script converts each raw example into one Axolotl training row.

## Expected fields

```json
{
  "source_id": "aie-debug-001",
  "split": "train",
  "task_type": "debug",
  "difficulty": "medium",
  "platform": "versal",
  "kernel_language": "aie-cpp",
  "tags": ["graph", "window", "compile-error"],
  "system": "You are an expert on AMD/Xilinx Versal AIE kernel debugging...",
  "user": {
    "prompt": "Optional free-form prompt if you already wrote it out.",
    "goal": "What should the model do?",
    "context": "Short natural-language setup.",
    "artifacts": {
      "graph_cpp": "...",
      "kernel_cpp": "...",
      "error_log": "..."
    },
    "constraints": [
      "Focus on debugging the AIE issue.",
      "Suggest a concrete fix."
    ]
  },
  "assistant": {
    "response": "Ground-truth answer the model should learn to produce."
  }
}
```

## Notes

- If `user.prompt` is present, the builder uses it directly.
- Otherwise, the builder assembles a prompt from `goal`, `context`, `artifacts`, and `constraints`.
- Extra top-level fields are preserved inside `metadata` in the processed dataset.

## Quality bar

- Prefer realistic AIE bugs over toy syntax mistakes.
- Keep answers specific to the provided artifacts.
- Include the corrected code pattern when a fix is needed.
- Avoid unsupported claims when logs do not justify them.
- Default the task framing toward diagnosis, root cause, and concrete fixes.
