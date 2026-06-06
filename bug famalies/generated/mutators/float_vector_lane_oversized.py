import re
import copy

BUG_FAMILY = {
    "family_id": "BF203",
    "bug_type": "float_vector_lane_oversized",
    "category": "vector_lane_widths",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::vector<float,8>",
        "aie::vector<float,16>",
        "writeincr_v<8>",
        "writeincr_v<16>"
    ],
    "mutation_strategy": "Change the lane count for float vectors from a valid value (8 or 16) to an oversized value like 64 or 128 that exceeds the maximum supported vector width for float on AIE architecture.",
    "repair_expectation": "Reduce the lane count to a valid value (8 or 16) that fits within AIE vector register constraints for 32-bit float.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating unsupported vector size for float type or exceeding maximum register width.",
    "tags": [
        "float",
        "lane_count",
        "oversized",
        "vector_lane_widths",
        "vector_register",
        "writeincr_v"
    ]
}

# Map valid lane counts to oversized replacements
_OVERSIZED_MAP = {
    "8": "64",
    "16": "128"
}

# Patterns to match the target constructs
_PATTERNS = [
    # aie::vector<float, 8> or aie::vector<float,16> (with optional whitespace)
    (re.compile(r'aie::vector<\s*float\s*,\s*(8|16)\s*>'), "aie::vector<float,{}>"),
    # writeincr_v<8> or writeincr_v<16>
    (re.compile(r'writeincr_v<\s*(8|16)\s*>'), "writeincr_v<{}>"),
]


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic to identify kernel source files (C/C++ for AIE)."""
    lower = file_path.lower()
    # Common AIE kernel file extensions
    if lower.endswith(('.cpp', '.cc', '.c', '.h', '.hpp', '.hh')):
        return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        for pattern, replacement_template in _PATTERNS:
            for match in pattern.finditer(content):
                lane_count = match.group(1)  # "8" or "16"
                oversized = _OVERSIZED_MAP[lane_count]
                original = match.group(0)

                # Build the replacement string
                # Reconstruct with the oversized lane count
                if "aie::vector" in replacement_template:
                    replacement = replacement_template.format(oversized)
                else:
                    replacement = replacement_template.format(oversized)

                start = match.start()
                end = match.end()

                description = (
                    f"Change float vector lane count from {lane_count} to {oversized} "
                    f"in '{original}', exceeding maximum supported vector width for "
                    f"float on AIE architecture."
                )

                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": description,
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

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
