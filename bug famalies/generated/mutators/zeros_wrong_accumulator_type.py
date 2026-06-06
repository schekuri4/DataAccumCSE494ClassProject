import re
import copy

BUG_FAMILY = {
    "family_id": "BF241",
    "bug_type": "zeros_wrong_accumulator_type",
    "category": "accumulator_initialization",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::zeros<acc48",
        "aie::zeros<acc80",
        "aie::zeros<accfloat",
        "aie::accum"
    ],
    "mutation_strategy": "Replace aie::zeros<acc48, N>() with aie::zeros<acc80, N>() or vice versa where the downstream operation (e.g., mac, addmac, or srs) expects a specific accumulator width, causing a type mismatch at compile time.",
    "repair_expectation": "Restore the correct accumulator element type (acc48 or acc80) matching the downstream intrinsic or API expectation.",
    "validation_signal": "WSL Vitis/AIE compile failure with template argument deduction or type mismatch error on the accumulator type.",
    "tags": [
        "acc48",
        "acc80",
        "accumulator",
        "accumulator_initialization",
        "type_mismatch",
        "zeros"
    ]
}

# Patterns for aie::zeros<accXX, N>() and aie::accum<accXX, N>
_ZEROS_PATTERN = re.compile(
    r'(aie::zeros\s*<\s*)(acc48|acc80|accfloat)(\s*,\s*[^>]+>)'
)

_ACCUM_PATTERN = re.compile(
    r'(aie::accum\s*<\s*)(acc48|acc80|accfloat)(\s*,\s*[^>]+>)'
)

# Mapping for type swaps
_SWAP_MAP = {
    "acc48": "acc80",
    "acc80": "acc48",
    "accfloat": "acc80",
}


def _is_kernel_source(file_path):
    """Heuristic: kernel source files are .cc, .cpp, .h, .hpp files."""
    lower = file_path.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp', '.hxx'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Search for aie::zeros<accXX, N>() patterns
        for match in _ZEROS_PATTERN.finditer(content):
            original_type = match.group(2)
            replacement_type = _SWAP_MAP.get(original_type)
            if replacement_type is None:
                continue

            original_text = match.group(0)
            replacement_text = match.group(1) + replacement_type + match.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": "zeros_wrong_accumulator_type",
                "category": "accumulator_initialization",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Replace aie::zeros<{original_type},...> with "
                    f"aie::zeros<{replacement_type},...> to introduce accumulator type mismatch"
                )
            })

        # Search for aie::accum<accXX, N> patterns
        for match in _ACCUM_PATTERN.finditer(content):
            original_type = match.group(2)
            replacement_type = _SWAP_MAP.get(original_type)
            if replacement_type is None:
                continue

            original_text = match.group(0)
            replacement_text = match.group(1) + replacement_type + match.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": "zeros_wrong_accumulator_type",
                "category": "accumulator_initialization",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Replace aie::accum<{original_type},...> with "
                    f"aie::accum<{replacement_type},...> to introduce accumulator type mismatch"
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    if file_path not in project_files:
        return dict(project_files)

    content = project_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    else:
        new_content = content[:start] + candidate["replacement"] + content[end:]

    # Return a new dict without mutating the input
    result = dict(project_files)
    result[file_path] = new_content
    return result
