import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF262",
    "bug_type": "sliding_mul_lane_count_invalid",
    "category": "sliding_mul_and_mac",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::sliding_mul_ops",
        "aie::sliding_mac_ops",
        "Lanes"
    ],
    "mutation_strategy": "Modify the Lanes template parameter to an unsupported value for the given data type combination. For example, set Lanes=6 for int16 x int16 (valid values are 4 or 8), or Lanes=3 for int32 x int16 (valid is 4 or 8).",
    "repair_expectation": "Correct the Lanes template parameter to a hardware-supported lane count for the specific data type combination used.",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert or template substitution failure indicating invalid Lanes parameter.",
    "tags": [
        "architecture_constraint",
        "lanes",
        "sliding_mul",
        "sliding_mul_and_mac",
        "template_parameter"
    ]
}

# Map valid lane counts to invalid replacements
_INVALID_LANES = {
    "4": "6",
    "8": "6",
    "2": "3",
    "16": "6",
}

# Fallback: if the current value isn't in our map, use these known-bad values
_DEFAULT_INVALID = "6"


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files likely containing AIE code."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern matches aie::sliding_mul_ops< Lanes, ... > or aie::sliding_mac_ops< Lanes, ... >
    # The Lanes parameter is the first template argument.
    # We look for the pattern: aie::sliding_mul_ops<NUMBER or aie::sliding_mac_ops<NUMBER
    pattern = re.compile(
        r'(aie::sliding_m(?:ul|ac)_ops\s*<\s*)(\d+)'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file contains relevant constructs
        if 'sliding_mul_ops' not in content and 'sliding_mac_ops' not in content:
            continue

        for match in pattern.finditer(content):
            lanes_value = match.group(2)
            start_of_lanes = match.start(2)
            end_of_lanes = match.end(2)

            # Determine invalid replacement
            if lanes_value in _INVALID_LANES:
                replacement = _INVALID_LANES[lanes_value]
            else:
                # If it's already an invalid value, skip
                # Pick a value that's definitely invalid
                replacement = _DEFAULT_INVALID
                if replacement == lanes_value:
                    replacement = "3"

            # Don't create a no-op mutation
            if replacement == lanes_value:
                continue

            full_match_text = match.group(0)
            op_type = "sliding_mul_ops" if "sliding_mul_ops" in full_match_text else "sliding_mac_ops"

            candidate = {
                "file_path": file_path,
                "bug_type": "sliding_mul_lane_count_invalid",
                "category": "sliding_mul_and_mac",
                "start": start_of_lanes,
                "end": end_of_lanes,
                "original": lanes_value,
                "replacement": replacement,
                "description": (
                    f"Changed Lanes template parameter of aie::{op_type} from "
                    f"{lanes_value} to {replacement} (unsupported lane count) "
                    f"in {file_path}"
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
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        pattern = re.compile(
            r'(aie::sliding_m(?:ul|ac)_ops\s*<\s*)' + re.escape(original) + r'(?=\s*[,>])'
        )
        match = pattern.search(content)
        if match:
            start = match.start(0) + len(match.group(1))
            end = start + len(original)
        else:
            return new_files  # Cannot apply mutation safely

    new_content = content[:start] + replacement + content[end:]
    new_files[file_path] = new_content

    return new_files
