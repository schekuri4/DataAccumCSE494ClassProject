import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF245",
    "bug_type": "accum_to_vector_invalid_shift_value",
    "category": "accumulator_initialization",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        ".to_vector<int16>(",
        ".to_vector<int32>(",
        "srs(",
        "aie::accum"
    ],
    "mutation_strategy": "Pass a negative shift value or a shift value exceeding the accumulator width (e.g., -1 or 64 for acc48) to accum.to_vector<T>(shift), causing a compile-time static_assert or constexpr bounds check failure.",
    "repair_expectation": "Use a valid non-negative shift value within the supported range for the accumulator width (typically 0 to accumulator_bits - output_bits).",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert on shift range or constexpr evaluation error.",
    "tags": [
        "accumulator",
        "accumulator_initialization",
        "out_of_range",
        "shift",
        "srs",
        "to_vector"
    ]
}


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic to identify kernel source files (C/C++ for AIE)."""
    extensions = ('.cc', '.cpp', '.c', '.h', '.hpp', '.cxx')
    return file_path.lower().endswith(extensions)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Pattern 1: .to_vector<T>(shift_value)
        # Matches expressions like acc.to_vector<int16>(shift) or .to_vector<int32>(0)
        to_vector_pattern = re.compile(
            r'(\.to_vector\s*<\s*(?:int16|int32|int8|uint8|uint16|uint32)\s*>\s*\()([^)]*)\)'
        )
        for match in to_vector_pattern.finditer(content):
            shift_arg = match.group(2).strip()
            if not shift_arg:
                # No argument provided, skip
                continue
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            # Replace the shift value with an invalid one (-1)
            prefix = match.group(1)
            replacement = prefix + "-1)"
            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_to_vector_invalid_shift_value",
                "category": "accumulator_initialization",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": f"Replace valid shift value '{shift_arg}' with -1 in to_vector call, causing out-of-range shift error."
            })
            # Also add a candidate with excessively large shift (64)
            replacement_large = prefix + "64)"
            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_to_vector_invalid_shift_value",
                "category": "accumulator_initialization",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement_large,
                "description": f"Replace valid shift value '{shift_arg}' with 64 in to_vector call, exceeding accumulator width."
            })

        # Pattern 2: srs(accumulator_expr, shift_value)
        # Matches aie::srs(..., shift) or srs(..., shift)
        srs_pattern = re.compile(
            r'((?:aie::)?srs\s*\()([^,]+),\s*([^)]+)\)'
        )
        for match in srs_pattern.finditer(content):
            shift_arg = match.group(3).strip()
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            prefix = match.group(1)
            acc_arg = match.group(2)
            # Replace shift with -1
            replacement = prefix + acc_arg + ", -1)"
            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_to_vector_invalid_shift_value",
                "category": "accumulator_initialization",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": f"Replace valid shift value '{shift_arg}' with -1 in srs() call, causing invalid shift error."
            })
            # Also with 64
            replacement_large = prefix + acc_arg + ", 64)"
            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_to_vector_invalid_shift_value",
                "category": "accumulator_initialization",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement_large,
                "description": f"Replace valid shift value '{shift_arg}' with 64 in srs() call, exceeding accumulator width."
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
