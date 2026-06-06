import re
import copy

BUG_FAMILY = {
    "family_id": "BF113",
    "bug_type": "scalar_readincr_on_vector_port",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr(",
        "input_stream<",
        "get_ss(",
        "input_stream_int32"
    ],
    "mutation_strategy": "Replace a vector read operation (readincr_v<N>) with a scalar readincr() call on the same stream pointer, causing API confusion between scalar and vector stream access patterns.",
    "repair_expectation": "Replace the scalar readincr() with the correct readincr_v<N>() call using the appropriate lane count for the stream width.",
    "validation_signal": "WSL Vitis/AIE compile failure due to return type incompatibility when assigning scalar result to a vector variable, or functional mismatch.",
    "tags": [
        "api_mismatch",
        "readincr",
        "scalar_vector_confusion",
        "stream_vector_interfaces"
    ]
}

# Pattern to match readincr_v<N>(...) calls
# Captures: full match, lane count N, and the stream argument
READINCR_V_PATTERN = re.compile(
    r'readincr_v<(\d+)>\s*\(\s*([^)]+)\s*\)'
)

# Also match get_ss<...>(...) which is another vector stream read pattern
GET_SS_PATTERN = re.compile(
    r'get_ss<([^>]+)>\s*\(\s*([^)]+)\s*\)'
)


def _is_kernel_source(file_path):
    """Heuristic to identify kernel source files (C/C++ for AIE)."""
    extensions = ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx', '.cxx')
    return file_path.lower().endswith(extensions)


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Find readincr_v<N>(stream) patterns and propose replacing with readincr(stream)
        for match in READINCR_V_PATTERN.finditer(content):
            lane_count = match.group(1)
            stream_arg = match.group(2).strip()
            original = match.group(0)
            replacement = f'readincr({stream_arg})'

            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "scalar_readincr_on_vector_port",
                "category": "stream_vector_interfaces",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Replace vector read 'readincr_v<{lane_count}>({stream_arg})' "
                    f"with scalar 'readincr({stream_arg})' causing type mismatch "
                    f"between scalar return and expected vector<{lane_count}> result."
                )
            })

        # Find get_ss<type>(stream) patterns - another vector stream read
        for match in GET_SS_PATTERN.finditer(content):
            type_arg = match.group(1).strip()
            stream_arg = match.group(2).strip()
            original = match.group(0)
            # Replace with scalar readincr
            replacement = f'readincr({stream_arg})'

            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "scalar_readincr_on_vector_port",
                "category": "stream_vector_interfaces",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Replace vector stream read 'get_ss<{type_arg}>({stream_arg})' "
                    f"with scalar 'readincr({stream_arg})' causing API confusion "
                    f"between scalar and vector stream access patterns."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
