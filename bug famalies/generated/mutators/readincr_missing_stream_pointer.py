import re
import copy

BUG_FAMILY = {
    "family_id": "BF101",
    "bug_type": "readincr_missing_stream_pointer",
    "category": "stream_scalar_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr(",
        "input_stream_int32*",
        "input_stream_float*",
        "input_stream_int16*"
    ],
    "mutation_strategy": "Replace the stream pointer parameter (e.g., input_stream_int32* sin) with a plain scalar or reference type (e.g., int32 sin or int32& sin), causing readincr to receive a non-stream argument. The function signature and call site are both mutated.",
    "repair_expectation": "Restore the correct input_stream_<type>* pointer type for the kernel parameter used with readincr.",
    "validation_signal": "WSL Vitis/AIE compile failure with error indicating readincr expects a stream pointer type, not a scalar or reference.",
    "tags": [
        "parameter_type",
        "readincr",
        "scalar_api",
        "stream_pointer",
        "stream_scalar_interfaces"
    ]
}

# Map from stream type to scalar replacement type
_STREAM_TO_SCALAR = {
    "input_stream_int32": "int32",
    "input_stream_float": "float",
    "input_stream_int16": "int16",
    "input_stream<int32>": "int32",
    "input_stream<float>": "float",
    "input_stream<int16>": "int16",
    "input_stream<cint16>": "cint16",
}

# Pattern to match stream pointer parameter declarations like:
# input_stream_int32* varname
# input_stream_float * varname
# input_stream_int16 *varname
_STREAM_PARAM_PATTERN = re.compile(
    r'((?:adf::)?input_stream_(?:int32|float|int16)|(?:adf::)?input_stream\s*<\s*(?:int32|float|int16|cint16)\s*>)'
    r'\s*\*\s*(?:__restrict|restrict)?\s*(\w+)'
)


def _is_kernel_source(filepath):
    """Heuristic: consider .cpp, .cc, .c, .h, .hpp files as potential kernel sources."""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp'))


def _file_uses_readincr(content):
    """Check if file contains readincr calls."""
    return 'readincr(' in content or 'readincr_v' in content


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_kernel_source(filepath):
            continue
        if not _file_uses_readincr(content):
            continue

        # Find all stream pointer parameter declarations
        for match in _STREAM_PARAM_PATTERN.finditer(content):
            stream_type = match.group(1)  # e.g., input_stream_int32
            var_name = match.group(2)     # e.g., sin

            # Verify that readincr is called with this variable somewhere in the file
            # Look for readincr(var_name) or readincr( var_name )
            readincr_pattern = re.compile(r'readincr\s*\(\s*' + re.escape(var_name) + r'\s*[,)]')
            if not readincr_pattern.search(content):
                continue

            original_text = match.group(0)  # e.g., "input_stream_int32* sin"
            normalized_stream_type = re.sub(r'\s+', '', stream_type.replace('adf::', ''))
            scalar_type = _STREAM_TO_SCALAR.get(normalized_stream_type, "int32")

            # Replace with scalar reference type (e.g., int32& sin)
            replacement_text = f"{scalar_type}& {var_name}"

            start = match.start()
            end = match.end()

            candidate = {
                "file_path": filepath,
                "bug_type": "readincr_missing_stream_pointer",
                "category": "stream_scalar_interfaces",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Replace stream pointer parameter '{original_text}' with "
                    f"scalar reference '{replacement_text}', causing readincr to "
                    f"receive a non-stream argument."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = new_project_files[filepath]

    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[filepath] = new_content
    return new_project_files
