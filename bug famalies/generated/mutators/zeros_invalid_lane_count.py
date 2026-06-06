import re
import copy

BUG_FAMILY = {
    "family_id": "BF242",
    "bug_type": "zeros_invalid_lane_count",
    "category": "accumulator_initialization",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::zeros<acc48,",
        "aie::zeros<acc80,",
        "aie::accum<acc48,"
    ],
    "mutation_strategy": "Change the lane count template parameter of aie::zeros<accT, N>() to a value not supported by the architecture (e.g., 7, 3, 12 instead of 8, 16, 32), causing a static_assert or template instantiation failure.",
    "repair_expectation": "Set the lane count to an architecture-valid value (8, 16, or 32 depending on accumulator element type).",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert or invalid template instantiation for unsupported vector/accumulator size.",
    "tags": [
        "accumulator_initialization",
        "lane_count",
        "static_assert",
        "template",
        "zeros"
    ]
}

# Valid lane counts that we want to mutate away from
VALID_LANE_COUNTS = {8, 16, 32}

# Invalid replacements mapped from valid values
INVALID_REPLACEMENTS = {
    "8": "7",
    "16": "12",
    "32": "3",
}

# Pattern matches aie::zeros<acc48, 8> or aie::zeros<acc80, 16> or aie::accum<acc48, 32>
# Captures the full match with the lane count as a group
_PATTERN = re.compile(
    r'(aie::(?:zeros|accum)\s*<\s*acc(?:48|80)\s*,\s*)(\d+)(\s*>)'
)


def _is_kernel_source(file_path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = file_path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx'))


def _pick_invalid_lane_count(original_value):
    """Pick an invalid lane count given the original valid one."""
    orig_str = str(original_value).strip()
    if orig_str in INVALID_REPLACEMENTS:
        return INVALID_REPLACEMENTS[orig_str]
    # If it's already valid but not in our map, just use 7
    try:
        val = int(orig_str)
        if val in VALID_LANE_COUNTS:
            return "7"
    except ValueError:
        pass
    return "7"


def find_mutation_candidates(project_files):
    candidates = []
    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue
        for match in _PATTERN.finditer(content):
            lane_count_str = match.group(2).strip()
            # We mutate any lane count we find (valid or not, but prefer valid ones)
            try:
                lane_val = int(lane_count_str)
            except ValueError:
                continue

            # Only mutate if the current value is architecturally valid
            if lane_val not in VALID_LANE_COUNTS:
                continue

            invalid_replacement = _pick_invalid_lane_count(lane_count_str)
            original_full = match.group(0)
            replacement_full = match.group(1) + invalid_replacement + match.group(3)

            candidate = {
                "file_path": file_path,
                "bug_type": "zeros_invalid_lane_count",
                "category": "accumulator_initialization",
                "start": match.start(),
                "end": match.end(),
                "original": original_full,
                "replacement": replacement_full,
                "description": (
                    f"Changed lane count from {lane_count_str} to {invalid_replacement} "
                    f"in '{original_full}' at offset {match.start()}, introducing an "
                    f"unsupported accumulator size that will cause a template instantiation failure."
                )
            }
            candidates.append(candidate)
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
