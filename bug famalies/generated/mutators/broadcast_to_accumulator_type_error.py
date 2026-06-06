import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF243",
    "bug_type": "broadcast_to_accumulator_type_error",
    "category": "accumulator_initialization",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["aie::broadcast<", "aie::accum", "aie::zeros"],
    "mutation_strategy": "Replace aie::zeros initialization with aie::broadcast<int32>(0) and assign directly to an aie::accum variable, causing a compile-time error because broadcast returns a vector, not an accumulator.",
    "repair_expectation": "Use aie::zeros<accT, N>() for accumulator initialization, or explicitly convert the broadcast vector to an accumulator via from_vector with appropriate shift.",
    "validation_signal": "WSL Vitis/AIE compile failure with no viable conversion from aie::vector to aie::accum.",
    "tags": ["accumulator", "accumulator_initialization", "broadcast", "type_conversion", "zeros"],
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match aie::zeros<...>() calls
    # e.g., aie::zeros<acc48, 8>(), aie::zeros<cacc48,16>()
    zeros_pattern = re.compile(
        r'aie::zeros\s*<\s*([^>]+)\s*>\s*\(\s*\)'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        for match in zeros_pattern.finditer(content):
            original = match.group(0)
            template_args = match.group(1).strip()

            # Parse template arguments: typically <accum_type, lanes>
            # We want to replace with aie::broadcast<int32>(0)
            # which returns a vector, not an accumulator
            start = match.start()
            end = match.end()

            replacement = "aie::broadcast<int32>(0)"

            description = (
                f"Replace '{original}' with '{replacement}'. "
                f"This causes a type error because aie::broadcast returns a vector, "
                f"not an accumulator, so assigning it to an aie::accum variable will fail."
            )

            candidates.append({
                "file_path": file_path,
                "bug_type": "broadcast_to_accumulator_type_error",
                "category": "accumulator_initialization",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": description,
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
