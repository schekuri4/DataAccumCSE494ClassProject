BUG_FAMILY = {
    "family_id": "BF103",
    "bug_type": "input_stream_used_as_output_stream",
    "category": "stream_scalar_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "writeincr(",
        "input_stream_int32*",
        "input_stream_float*",
        "readincr(",
        "output_stream_int32*"
    ],
    "mutation_strategy": "Swap the stream direction: use an input_stream pointer with writeincr, or an output_stream pointer with readincr. The kernel parameter type remains unchanged but the API call uses the wrong direction.",
    "repair_expectation": "Match the stream API call to the correct stream direction (readincr with input_stream, writeincr with output_stream).",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch error indicating incompatible stream pointer direction for the scalar stream API.",
    "tags": [
        "direction_mismatch",
        "readincr",
        "stream_scalar_interfaces",
        "stream_type",
        "writeincr"
    ]
}

import re
from copy import deepcopy


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cc, .cpp, .c, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find readincr() calls and replace with writeincr()
        # This creates a bug where an input_stream is used with writeincr
        for match in re.finditer(r'\breadincr\s*\(', content):
            start = match.start()
            end = match.end()
            original = match.group(0)
            # Replace readincr( with writeincr(
            replacement = re.sub(r'readincr\s*\(', 'writeincr(', original)
            candidates.append({
                "file_path": file_path,
                "bug_type": "input_stream_used_as_output_stream",
                "category": "stream_scalar_interfaces",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": "Replaced readincr() with writeincr() causing input_stream to be used with output API."
            })

        # Strategy 2: Find writeincr() calls and replace with readincr()
        # This creates a bug where an output_stream is used with readincr
        for match in re.finditer(r'\bwriteincr\s*\(', content):
            start = match.start()
            end = match.end()
            original = match.group(0)
            replacement = re.sub(r'writeincr\s*\(', 'readincr(', original)
            candidates.append({
                "file_path": file_path,
                "bug_type": "input_stream_used_as_output_stream",
                "category": "stream_scalar_interfaces",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": "Replaced writeincr() with readincr() causing output_stream to be used with input API."
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected location
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
