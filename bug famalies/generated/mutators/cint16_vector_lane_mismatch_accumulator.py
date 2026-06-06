import re
import copy

BUG_FAMILY = {
    "family_id": "BF204",
    "bug_type": "cint16_vector_lane_mismatch_accumulator",
    "category": "vector_lane_widths",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::vector<cint16,8>",
        "aie::vector<cint16,16>",
        "aie::accum<acc48,8>",
        "aie::accum<acc48,16>"
    ],
    "mutation_strategy": "Change the lane count of an acc48 accumulator so it does not match the lane count of the cint16 vector it is being assigned to or extracted from. For example, use aie::accum<acc48,16> with aie::vector<cint16,8> in a to_vector() or from_vector() call.",
    "repair_expectation": "Align the accumulator lane count with the vector lane count so both have the same number of lanes.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch or lane count mismatch error between accumulator and vector.",
    "tags": ["acc48", "accumulator", "cint16", "complex", "lane_mismatch", "vector_lane_widths"]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, or .h files."""
    return path.endswith(('.cpp', '.cc', '.h', '.hpp'))


def _flip_lane_count(lane_str):
    """Flip between 8 and 16."""
    if lane_str == '8':
        return '16'
    elif lane_str == '16':
        return '8'
    return None


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to find acc48 accumulator declarations with lane count 8 or 16
    accum_pattern = re.compile(
        r'aie::accum\s*<\s*acc48\s*,\s*(8|16)\s*>'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file contains cint16 vectors (context for the bug)
        has_cint16_vector = bool(re.search(r'aie::vector\s*<\s*cint16\s*,\s*(8|16)\s*>', content))
        if not has_cint16_vector:
            continue

        for match in accum_pattern.finditer(content):
            lane_count = match.group(1)
            new_lane_count = _flip_lane_count(lane_count)
            if new_lane_count is None:
                continue

            original = match.group(0)
            # Build replacement by substituting the lane count
            replacement = re.sub(
                r'(aie::accum\s*<\s*acc48\s*,\s*)' + re.escape(lane_count) + r'(\s*>)',
                r'\g<1>' + new_lane_count + r'\2',
                original
            )

            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed acc48 accumulator lane count from {lane_count} to "
                    f"{new_lane_count}, creating a mismatch with the cint16 vector "
                    f"lane count in {file_path}."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_project_files = dict(project_files)

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

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

    new_project_files[file_path] = new_content
    return new_project_files
