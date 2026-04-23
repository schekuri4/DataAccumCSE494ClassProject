import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "examples"
PROCESSED_DIR = ROOT / "data" / "processed"


def format_artifacts(artifacts: dict) -> str:
    parts = []
    for name, content in artifacts.items():
        if not content:
            continue
        language = "cpp" if name.endswith(("_cpp", ".cpp")) else "text"
        parts.append(f"### {name}\n```{language}\n{content.rstrip()}\n```")
    return "\n\n".join(parts)


def format_constraints(constraints: list[str]) -> str:
    if not constraints:
        return ""
    return "\n".join(f"- {item}" for item in constraints)


def build_user_prompt(user_payload: dict) -> str:
    direct_prompt = user_payload.get("prompt")
    if direct_prompt:
        return direct_prompt.strip()

    sections = []

    goal = user_payload.get("goal")
    if goal:
        sections.append(f"Task: {goal.strip()}")

    context = user_payload.get("context")
    if context:
        sections.append(f"Context:\n{context.strip()}")

    artifacts = format_artifacts(user_payload.get("artifacts", {}))
    if artifacts:
        sections.append(f"Artifacts:\n{artifacts}")

    constraints = format_constraints(user_payload.get("constraints", []))
    if constraints:
        sections.append(f"Constraints:\n{constraints}")

    return "\n\n".join(sections).strip()


def convert_example(example: dict) -> dict:
    processed = {
        "messages": [
            {
                "role": "system",
                "content": example["system"].strip(),
            },
            {
                "role": "user",
                "content": build_user_prompt(example["user"]),
            },
            {
                "role": "assistant",
                "content": example["assistant"]["response"].strip(),
            },
        ],
        "metadata": {
            "source_id": example.get("source_id"),
            "task_type": example.get("task_type"),
            "difficulty": example.get("difficulty"),
            "platform": example.get("platform"),
            "kernel_language": example.get("kernel_language"),
            "tags": example.get("tags", []),
            "split": example.get("split", "train"),
        },
    }
    return processed


def load_examples() -> list[dict]:
    examples = []
    for path in sorted(RAW_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            examples.append(json.load(handle))
    return examples


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True))
            handle.write("\n")


def main() -> None:
    examples = load_examples()
    processed_rows = [convert_example(example) for example in examples]

    train_rows = [row for row in processed_rows if row["metadata"]["split"] == "train"]
    validation_rows = [row for row in processed_rows if row["metadata"]["split"] == "validation"]

    write_jsonl(PROCESSED_DIR / "train.jsonl", train_rows)
    write_jsonl(PROCESSED_DIR / "validation.jsonl", validation_rows)

    print(f"Wrote {len(train_rows)} train rows and {len(validation_rows)} validation rows.")


if __name__ == "__main__":
    main()