import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF205",
    "bug_type": "int16_shuffle_up_invalid_lane_count",
    "category": "vector_lane_widths",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::shuffle_up",
        "aie::shuffle_down",
        "::aie::shuffle_up",
        "::aie::shuffle_down",
        "aie::vector<int16,",
    ],
    "mutation_strategy": "Apply aie::shuffle_up or aie::shuffle_down on an int16 vector whose lane count is not a supported width for shuffle operations (e.g., use a 4-lane int16 vector when only 16 or 32 lanes are supported for shuffle intrinsics).",
    "repair_expectation": "Change the vector lane count to a supported width for shuffle operations (16 or 32 for int16) or restructure the code to use a valid vector size.",
    "validation_signal": "WSL Vitis/AIE compile failure with no matching function or unsupported vector size for shuffle_up/shuffle_down.",
    "tags": [
        "int16",
        "intrinsic",
        "lane_count",
        "shuffle_down",
        "shuffle_up",
        "vector_lane_widths",
    ],
}

# Supported lane counts for int16 shuffle operations
SUPPORTED_LANE_COUNTS = {16, 32}
# Invalid lane count to inject
INVALID_LANE_COUNT = 4


def _is_kernel_source(filepath: str) -> bool:
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for filepath, content in project_files.items():
        if not _is_kernel_source(filepath):
            continue

        # Strategy 1: Find aie::vector<int16, N> declarations where N is a supported
        # lane count, and mutate N to an invalid lane count.
        # This targets vectors that are likely used with shuffle_up/shuffle_down.
        pattern_vector = re.compile(
            r'((?:::)?aie::vector\s*<\s*(?:int16|int16_t)\s*,\s*)(\d+)(\s*>)'
        )
        for match in pattern_vector.finditer(content):
            lane_count = int(match.group(2))
            if lane_count in SUPPORTED_LANE_COUNTS:
                # Check if shuffle_up or shuffle_down appears in the file
                if 'shuffle_up' in content or 'shuffle_down' in content:
                    original = match.group(0)
                    replacement = match.group(1) + str(INVALID_LANE_COUNT) + match.group(3)
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "int16_shuffle_up_invalid_lane_count",
                        "category": "vector_lane_widths",
                        "start": match.start(),
                        "end": match.end(),
                        "original": original,
                        "replacement": replacement,
                        "description": (
                            f"Changed int16 vector lane count from {lane_count} to "
                            f"{INVALID_LANE_COUNT} (unsupported for shuffle_up/shuffle_down). "
                            f"Only 16 or 32 lanes are valid for int16 shuffle operations."
                        ),
                    })

        # Strategy 2: If there are shuffle_up/shuffle_down calls but no vector decl
        # with supported lane count was found, look for shuffle calls directly and
        # try to find the vector type nearby to mutate.
        # Also handle cases where the vector is declared with a template alias or
        # the lane count appears in a shuffle call context.
        pattern_shuffle = re.compile(
            r'((?:::)?aie::shuffle_(?:up|down)(?:_fill|_rotate)?\s*\(\s*\w+\s*,\s*)(\d+)(\s*\))'
        )
        for match in pattern_shuffle.finditer(content):
            # The second argument is the shift amount, not lane count.
            # We look for vector declarations associated with this usage.
            pass

        # Strategy 3: Find shuffle_up/shuffle_down calls on vectors and if the
        # vector type is templated inline like aie::shuffle_up(aie::vector<int16,N>...)
        pattern_inline = re.compile(
            r'((?:::)?aie::shuffle_(?:up|down)(?:_fill|_rotate)?\s*[^;]*(?:::)?aie::vector\s*<\s*(?:int16|int16_t)\s*,\s*)(\d+)(\s*>)'
        )
        for match in pattern_inline.finditer(content):
            lane_count = int(match.group(2))
            if lane_count in SUPPORTED_LANE_COUNTS:
                original = match.group(0)
                replacement = match.group(1) + str(INVALID_LANE_COUNT) + match.group(3)
                # Avoid duplicates
                already_exists = any(
                    c["file_path"] == filepath and c["start"] == match.start()
                    for c in candidates
                )
                if not already_exists:
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "int16_shuffle_up_invalid_lane_count",
                        "category": "vector_lane_widths",
                        "start": match.start(),
                        "end": match.end(),
                        "original": original,
                        "replacement": replacement,
                        "description": (
                            f"Changed int16 vector lane count from {lane_count} to "
                            f"{INVALID_LANE_COUNT} in shuffle call (unsupported width)."
                        ),
                    })

    return candidates


def apply_mutation(
    project_files: dict[str, str], candidate: dict[str, object]
) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[filepath] = new_content
    return new_files
