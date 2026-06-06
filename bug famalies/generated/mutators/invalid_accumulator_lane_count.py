import re
import copy

BUG_FAMILY = {
    "family_id": "BF232",
    "bug_type": "invalid_accumulator_lane_count",
    "category": "accumulator_types",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::accum<acc48,",
        "aie::accum<acc80,",
        "aie::accum<acc64,"
    ],
    "mutation_strategy": "Change the lane count template parameter of an aie::accum declaration to a value not supported by the AIE architecture (e.g., change 8 to 12, or 16 to 10), violating the power-of-two or architecture-specific lane count constraints.",
    "repair_expectation": "Restore the lane count to a valid value for the target accumulator type (e.g., 4, 8, 16, or 32 depending on the accumulator width and AIE variant).",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert or template substitution failure indicating unsupported lane count.",
    "tags": [
        "accumulator_types",
        "architecture_constraint",
        "lane_count",
        "static_assert",
        "template_parameter"
    ]
}

# Map valid lane counts to invalid replacements
_INVALID_LANE_MAP = {
    "4": "6",
    "8": "12",
    "16": "10",
    "32": "24",
    "64": "48",
    "2": "3",
}


def _get_invalid_lane_count(valid_count_str):
    """Return an invalid lane count for a given valid one."""
    stripped = valid_count_str.strip()
    if stripped in _INVALID_LANE_MAP:
        return _INVALID_LANE_MAP[stripped]
    # For any other numeric value, add 1 to make it odd/invalid
    try:
        val = int(stripped)
        # Make it non-power-of-two
        if val > 1:
            return str(val + 1)
        return "3"
    except ValueError:
        return None


def find_mutation_candidates(project_files):
    candidates = []
    # Pattern matches aie::accum<acc48, N>, aie::accum<acc80, N>, aie::accum<acc64, N>
    # where N is a numeric lane count
    pattern = re.compile(
        r'(aie::accum<\s*acc(?:48|80|64)\s*,\s*)(\d+)(\s*>)'
    )

    for file_path, content in project_files.items():
        # Target kernel source files (typically .cc, .cpp, .h, .hpp)
        if not any(file_path.endswith(ext) for ext in ('.cc', '.cpp', '.h', '.hpp', '.c', '.cxx')):
            continue

        for match in pattern.finditer(content):
            original_lane_str = match.group(2)
            invalid_lane = _get_invalid_lane_count(original_lane_str)
            if invalid_lane is None:
                continue

            original_full = match.group(0)
            replacement_full = match.group(1) + invalid_lane + match.group(3)

            start = match.start()
            end = match.end()

            # Determine which acc type for description
            acc_type_match = re.search(r'acc(?:48|80|64)', match.group(1))
            acc_type = acc_type_match.group(0) if acc_type_match else "accXX"

            candidates.append({
                "file_path": file_path,
                "bug_type": "invalid_accumulator_lane_count",
                "category": "accumulator_types",
                "start": start,
                "end": end,
                "original": original_full,
                "replacement": replacement_full,
                "description": (
                    f"Changed aie::accum<{acc_type}, {original_lane_str.strip()}> lane count "
                    f"from {original_lane_str.strip()} to {invalid_lane}, which is not a valid "
                    f"lane count for the AIE architecture."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    # Verify the original text is at the expected position
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
