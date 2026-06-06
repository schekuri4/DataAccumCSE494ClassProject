import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF282",
    "bug_type": "complex_mac_accumulator_type_mismatch",
    "category": "complex_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::mac",
        "aie::accum",
        "cacc48",
        "cacc80",
        "acc48",
        "acc80"
    ],
    "mutation_strategy": "Change the accumulator type from cacc48 to acc48 (or cacc80 to acc80) when performing a complex multiply-accumulate, so the accumulator element type is real but the operands are complex (cint16/cint32), causing a type mismatch.",
    "repair_expectation": "Change the accumulator type back to the complex accumulator variant (cacc48 or cacc80) matching the complex operand types.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch between accumulator and complex multiplication result.",
    "tags": [
        "acc48",
        "accumulator",
        "cacc48",
        "complex",
        "complex_intrinsics",
        "mac",
        "type_mismatch"
    ]
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    # Pattern 1: cacc48 -> acc48
    # Pattern 2: cacc80 -> acc80
    # We look for occurrences of cacc48 or cacc80 as standalone type tokens
    # This covers declarations like: aie::accum<cacc48, N>, cacc48 variable declarations, etc.
    pattern = re.compile(r'\bcacc(48|80)\b')

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        for match in pattern.finditer(content):
            original = match.group(0)  # cacc48 or cacc80
            bits = match.group(1)      # 48 or 80
            replacement = f"acc{bits}"

            # Determine context - check if this is near a mac or accum usage
            # Get surrounding context (line)
            line_start = content.rfind('\n', 0, match.start()) + 1
            line_end = content.find('\n', match.end())
            if line_end == -1:
                line_end = len(content)
            line_content = content[line_start:line_end]

            # We accept all cacc48/cacc80 occurrences in kernel source as candidates
            # since they are likely accumulator type declarations used with complex MAC
            candidates.append({
                "file_path": file_path,
                "bug_type": "complex_mac_accumulator_type_mismatch",
                "category": "complex_intrinsics",
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Change complex accumulator type '{original}' to real accumulator "
                    f"type '{replacement}', causing a type mismatch with complex operands "
                    f"in multiply-accumulate operations. "
                    f"Context: {line_content.strip()}"
                )
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace the first occurrence
        idx = content.find(original)
        if idx == -1:
            return new_files
        start = idx
        end = idx + len(original)

    # Apply the mutation
    new_content = content[:start] + replacement + content[end:]
    new_files[file_path] = new_content

    return new_files
