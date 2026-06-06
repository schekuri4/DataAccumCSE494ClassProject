import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF233",
    "bug_type": "accum_zeros_wrong_template_args",
    "category": "accumulator_types",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::zeros<acc48",
        "aie::zeros<acc80",
        "aie::zeros<acc64",
        "aie::accum"
    ],
    "mutation_strategy": "Replace a valid aie::zeros<acc48, 8>() initialization with aie::zeros<int32, 8>() or aie::zeros<acc48, 7>(), using either a non-accumulator element type or an invalid lane count for the zeros factory function.",
    "repair_expectation": "Correct the template arguments to aie::zeros to use a valid accumulator tag (acc48/acc64/acc80) and a supported lane count.",
    "validation_signal": "WSL Vitis/AIE compile failure with template argument deduction failure or no matching function for aie::zeros.",
    "tags": [
        "accum_factory",
        "accumulator_types",
        "aie_zeros",
        "initialization",
        "template_mismatch"
    ]
}

# Pattern to match aie::zeros<accXX, N>() calls
_ZEROS_PATTERN = re.compile(
    r'aie::zeros\s*<\s*(acc(?:48|64|80))\s*,\s*(\d+)\s*>\s*\(\s*\)'
)

# Valid lane counts for AIE accumulators (powers of 2, common sizes)
_VALID_LANE_COUNTS = {4, 8, 16, 32}

# Non-accumulator types to use as replacements
_NON_ACCUM_TYPES = ["int32", "int16", "float", "cint16"]


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic to identify kernel source files."""
    extensions = ('.cc', '.cpp', '.c', '.h', '.hpp', '.hxx', '.cxx')
    lower = file_path.lower()
    # Accept any C/C++ source/header, especially those with 'kernel' in path
    if lower.endswith(extensions):
        return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        for match in _ZEROS_PATTERN.finditer(content):
            acc_type = match.group(1)
            lane_count_str = match.group(2)
            lane_count = int(lane_count_str)
            original = match.group(0)
            start = match.start()
            end = match.end()

            # Mutation strategy 1: Replace accumulator type with non-accumulator type
            replacement_type = _NON_ACCUM_TYPES[0]  # int32
            replacement_1 = f"aie::zeros<{replacement_type}, {lane_count_str}>()"
            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_zeros_wrong_template_args",
                "category": "accumulator_types",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement_1,
                "description": (
                    f"Replace valid aie::zeros<{acc_type}, {lane_count_str}>() with "
                    f"aie::zeros<{replacement_type}, {lane_count_str}>() — "
                    f"non-accumulator element type causes template argument deduction failure."
                )
            })

            # Mutation strategy 2: Replace lane count with invalid value
            # Pick a lane count that is not a power of 2 or is off-by-one
            invalid_lane = lane_count - 1 if lane_count > 1 else 3
            # Make sure it's actually different and likely invalid
            if invalid_lane in _VALID_LANE_COUNTS:
                invalid_lane = lane_count + 1
            replacement_2 = f"aie::zeros<{acc_type}, {invalid_lane}>()"
            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_zeros_wrong_template_args",
                "category": "accumulator_types",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement_2,
                "description": (
                    f"Replace valid aie::zeros<{acc_type}, {lane_count_str}>() with "
                    f"aie::zeros<{acc_type}, {invalid_lane}>() — "
                    f"invalid lane count causes no matching function error."
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
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
