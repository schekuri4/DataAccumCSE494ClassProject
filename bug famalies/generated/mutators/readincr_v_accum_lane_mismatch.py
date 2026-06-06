import re
import copy

BUG_FAMILY = {
    "family_id": "BF235",
    "bug_type": "readincr_v_accum_lane_mismatch",
    "category": "accumulator_types",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<",
        "aie::accum",
        "input_stream",
        "readincr_v8",
        "readincr_v16"
    ],
    "mutation_strategy": "Change the lane count in a readincr_v call so it does not match the accumulator lane count it is being assigned to (e.g., read 8 lanes from stream but assign to a 16-lane accumulator, or use readincr_v with a lane count unsupported for cascade/accumulator streams).",
    "repair_expectation": "Align the readincr_v lane count with the target accumulator's lane count and ensure the stream type matches.",
    "validation_signal": "WSL Vitis/AIE compile failure with vector/accumulator size mismatch or no matching overload for readincr_v assignment.",
    "tags": [
        "accumulator_types",
        "cascade",
        "lane_mismatch",
        "readincr_v",
        "stream"
    ]
}

# Common lane counts used in AIE designs
LANE_COUNTS = [4, 8, 16, 32]


def _is_kernel_source(filepath):
    """Heuristic to identify kernel source files."""
    exts = ('.cpp', '.cc', '.c', '.h', '.hpp')
    return any(filepath.endswith(ext) for ext in exts)


def _get_mismatched_lane(original_lane):
    """Return a different lane count to create a mismatch."""
    original_lane = int(original_lane)
    for candidate in LANE_COUNTS:
        if candidate != original_lane:
            return candidate
    # Fallback: double or halve
    if original_lane * 2 <= 32:
        return original_lane * 2
    return original_lane // 2


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern 1: readincr_v<type, lanes>(...) - template form
    pattern_template = re.compile(
        r'(readincr_v\s*<\s*[^,>]+\s*,\s*)(\d+)(\s*>)'
    )

    # Pattern 2: readincr_v8 or readincr_v16 etc. - suffix form
    pattern_suffix = re.compile(
        r'(readincr_v)(\d+)(\s*[\(<])'
    )

    # Pattern 3: readincr_v<type>(stream, lanes) - lane as function argument
    # e.g., readincr_v<acc48>(stream, 8)
    pattern_arg = re.compile(
        r'(readincr_v\s*<[^>]+>\s*\([^,]+,\s*)(\d+)(\s*\))'
    )

    for filepath, content in project_files.items():
        if not _is_kernel_source(filepath):
            continue

        # Check if file has relevant content
        has_readincr = 'readincr_v' in content
        if not has_readincr:
            continue

        lines = content.split('\n')

        for line_idx, line in enumerate(lines):
            # Search pattern 1: template lane count
            for m in pattern_template.finditer(line):
                original_lane = m.group(2)
                new_lane = _get_mismatched_lane(original_lane)
                if new_lane == int(original_lane):
                    continue

                # Calculate absolute positions
                line_start = sum(len(l) + 1 for l in lines[:line_idx])
                abs_start = line_start + m.start(2)
                abs_end = line_start + m.end(2)

                original_text = m.group(0)
                replacement_text = m.group(1) + str(new_lane) + m.group(3)

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "readincr_v_accum_lane_mismatch",
                    "category": "accumulator_types",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_lane,
                    "replacement": str(new_lane),
                    "description": (
                        f"Changed readincr_v lane count from {original_lane} to {new_lane} "
                        f"at line {line_idx + 1}, creating accumulator lane mismatch."
                    )
                })

            # Search pattern 2: suffix form (readincr_v8, readincr_v16)
            for m in pattern_suffix.finditer(line):
                original_lane = m.group(2)
                new_lane = _get_mismatched_lane(original_lane)
                if new_lane == int(original_lane):
                    continue

                line_start = sum(len(l) + 1 for l in lines[:line_idx])
                abs_start = line_start + m.start(2)
                abs_end = line_start + m.end(2)

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "readincr_v_accum_lane_mismatch",
                    "category": "accumulator_types",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_lane,
                    "replacement": str(new_lane),
                    "description": (
                        f"Changed readincr_v{original_lane} to readincr_v{new_lane} "
                        f"at line {line_idx + 1}, creating accumulator lane mismatch."
                    )
                })

            # Search pattern 3: lane as argument
            for m in pattern_arg.finditer(line):
                original_lane = m.group(2)
                new_lane = _get_mismatched_lane(original_lane)
                if new_lane == int(original_lane):
                    continue

                line_start = sum(len(l) + 1 for l in lines[:line_idx])
                abs_start = line_start + m.start(2)
                abs_end = line_start + m.end(2)

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "readincr_v_accum_lane_mismatch",
                    "category": "accumulator_types",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_lane,
                    "replacement": str(new_lane),
                    "description": (
                        f"Changed readincr_v argument lane count from {original_lane} to {new_lane} "
                        f"at line {line_idx + 1}, creating accumulator lane mismatch."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text != original:
        # Fallback: try to find and replace first occurrence
        # This handles potential offset issues
        new_content = content.replace(
            original, replacement, 1
        )
    else:
        new_content = content[:start] + replacement + content[end:]

    new_files[filepath] = new_content
    return new_files
