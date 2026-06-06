import re
import copy

BUG_FAMILY = {
    "family_id": "BF275",
    "bug_type": "readincr_v_complex_lane_mismatch",
    "category": "complex_datatypes",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<",
        "writeincr_v<",
        "cint16",
        "cint32",
        "input_stream"
    ],
    "mutation_strategy": "Change the lane count in readincr_v<N> or writeincr_v<N> to a value that is invalid for the complex data type width.",
    "repair_expectation": "Set the lane count to the correct value that matches the vector register width for the complex type (e.g., 8 for cint16 in 256-bit, 4 for cint32).",
    "validation_signal": "WSL Vitis/AIE compile failure with static assertion or template error about invalid vector size for the given complex element type.",
    "tags": [
        "cint16", "cint32", "complex_datatypes", "lane_count",
        "readincr_v", "stream", "writeincr_v"
    ]
}

# Valid lane counts for complex types in 256-bit vector registers
VALID_LANES = {
    "cint16": 8,
    "cint32": 4,
}

# Invalid replacement lane counts for each valid lane count
INVALID_REPLACEMENTS = {
    8: 16,   # For cint16: 16 lanes would exceed 256-bit register
    4: 8,    # For cint32: 8 lanes would exceed 256-bit register
}


def _is_kernel_source(filepath):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp in typical AIE projects."""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx'))


def _detect_complex_type_context(content, match_start, match_end):
    """Try to detect which complex type (cint16/cint32) is associated with a readincr_v/writeincr_v call."""
    # Look in a window around the match for complex type references
    window_start = max(0, match_start - 200)
    window_end = min(len(content), match_end + 200)
    window = content[window_start:window_end]

    # Check for cint32 first (more specific)
    if "cint32" in window:
        return "cint32"
    if "cint16" in window:
        return "cint16"
    return None


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern matches readincr_v<N> or writeincr_v<N>
    pattern = re.compile(r'((?:readincr_v|writeincr_v)\s*<\s*)(\d+)(\s*>)')

    for filepath, content in project_files.items():
        if not _is_kernel_source(filepath):
            continue

        # Check if file contains any complex type references
        has_complex = "cint16" in content or "cint32" in content
        if not has_complex:
            continue

        for match in pattern.finditer(content):
            prefix = match.group(1)
            lane_count_str = match.group(2)
            suffix = match.group(3)
            lane_count = int(lane_count_str)

            # Detect which complex type is in context
            complex_type = _detect_complex_type_context(content, match.start(), match.end())
            if complex_type is None:
                continue

            valid_lanes = VALID_LANES.get(complex_type)
            if valid_lanes is None:
                continue

            # Only mutate if the current lane count is valid (we want to introduce a bug)
            if lane_count != valid_lanes:
                continue

            invalid_lanes = INVALID_REPLACEMENTS.get(valid_lanes)
            if invalid_lanes is None:
                continue

            original = match.group(0)
            replacement = prefix + str(invalid_lanes) + suffix

            func_name = "readincr_v" if "readincr_v" in prefix else "writeincr_v"

            candidate = {
                "file_path": filepath,
                "bug_type": "readincr_v_complex_lane_mismatch",
                "category": "complex_datatypes",
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed {func_name} lane count from {valid_lanes} to {invalid_lanes} "
                    f"for {complex_type} stream, which exceeds the 256-bit vector register capacity."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[filepath] = new_content
    return new_files
