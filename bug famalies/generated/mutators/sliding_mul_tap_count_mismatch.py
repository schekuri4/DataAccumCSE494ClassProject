import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF261",
    "bug_type": "sliding_mul_tap_count_mismatch",
    "category": "sliding_mul_and_mac",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::sliding_mul_ops",
        "aie::sliding_mul_sym_ops",
        "Lanes",
        "Points"
    ],
    "mutation_strategy": "Change the Points (tap count) template parameter of aie::sliding_mul_ops or aie::sliding_mul_sym_ops to a value not supported by the architecture (e.g., change Points from 8 to 5 for int16 data, or from 4 to 3 for cint16). The value must violate the hardware-supported tap/point combinations.",
    "repair_expectation": "Restore the Points template parameter to a valid hardware-supported value (e.g., 4 or 8 depending on data type and lane configuration).",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or static_assert about unsupported Points/Lanes combination.",
    "tags": [
        "compile_time",
        "sliding_mul",
        "sliding_mul_and_mac",
        "tap_count",
        "template_parameter"
    ]
}

# Valid Points values commonly supported by AIE hardware
_VALID_POINTS = {4, 8, 16, 32}
_POINT_TOKENS = r'(?:\d+|Points|TP_POINTS|kPoints|points)'

# Map from valid points to an invalid replacement
def _get_invalid_points(original_val: int) -> int:
    """Return an invalid Points value given the original valid one."""
    # Choose a value that is definitely not hardware-supported
    if original_val == 4:
        return 3
    elif original_val == 8:
        return 5
    elif original_val == 16:
        return 11
    elif original_val == 32:
        return 17
    else:
        # If it's already unusual, just subtract 1
        return original_val - 1 if original_val > 1 else original_val + 1


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic to identify kernel source files (C/C++ for AIE)."""
    lower = file_path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match old ops-style APIs and modern direct calls:
    #   aie::sliding_mul_ops<Lanes, Points, ...>
    #   ::aie::sliding_mac<Lanes, Points, ...>(...)
    # We look for the template instantiation and try to identify the Lanes and Points parameters.
    # Common forms:
    #   aie::sliding_mul_ops<Lanes, Points, ...>
    #   aie::sliding_mul_sym_ops<Lanes, Points, ...>
    # The first two template params are typically Lanes and Points (integers).
    pattern = re.compile(
        r'((?:::)?aie::sliding_m(?:ul|ac)(?:_sym)?(?:_ops)?\s*<\s*)'  # group 1: prefix up to first param
        r'([^,>]+)'                                   # group 2: Lanes
        r'(\s*,\s*)'                                  # group 3: separator
        r'(' + _POINT_TOKENS + r')'                   # group 4: Points
        r'(\s*[,>])'                                  # group 5: rest (comma or closing >)
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file contains relevant constructs
        if 'sliding_mul' not in content and 'sliding_mac' not in content:
            continue

        for match in pattern.finditer(content):
            points_str = match.group(4)
            if points_str.isdigit():
                points_val = int(points_str)
                if points_val not in _VALID_POINTS:
                    continue
                invalid_points = _get_invalid_points(points_val)
            else:
                invalid_points = 5

            # Full matched text
            original_text = match.group(0)
            replacement_text = match.group(1) + match.group(2) + match.group(3) + str(invalid_points) + match.group(5)

            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mul_tap_count_mismatch",
                "category": "sliding_mul_and_mac",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed Points template parameter from {points_str} to {invalid_points} "
                    f"in {match.group(1).strip()} at offset {start}, "
                    f"introducing an unsupported tap count for the architecture."
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

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
