import re
import copy

BUG_FAMILY = {
    "family_id": "BF109",
    "bug_type": "stream_type_width_mismatch_in_readincr",
    "category": "stream_scalar_interfaces",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_stream_int32",
        "input_stream_int16",
        "input_stream_float",
        "readincr(",
        "writeincr("
    ],
    "mutation_strategy": "Change the stream type width in the kernel parameter (e.g., input_stream_int32* to input_stream_int16*) without updating the readincr usage or the graph port type, creating a type mismatch between the declared stream width and the expected data type in the graph connection.",
    "repair_expectation": "Ensure the stream type in the kernel signature matches both the readincr/writeincr usage and the graph port/PLIO width declaration.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch or implicit conversion error between stream types of different widths.",
    "tags": [
        "int32_vs_int16",
        "readincr",
        "stream_scalar_interfaces",
        "stream_width",
        "type_mismatch"
    ]
}

# Mapping of stream types to their possible mismatched replacements
_STREAM_TYPE_REPLACEMENTS = {
    "input_stream_int32": "input_stream_int16",
    "input_stream_int16": "input_stream_int32",
    "input_stream_float": "input_stream_int32",
    "output_stream_int32": "output_stream_int16",
    "output_stream_int16": "output_stream_int32",
    "output_stream_float": "output_stream_int32",
}

# Pattern to match stream type declarations in function parameters
_STREAM_PARAM_PATTERN = re.compile(
    r'\b((?:input|output)_stream_(?:int32|int16|float))\s*\*'
)


def _is_kernel_file(file_path):
    """Heuristic to identify kernel source/header files."""
    lower = file_path.lower()
    # Typical AIE kernel files are .cpp, .cc, .h, .hpp in kernel directories
    if any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh')):
        return True
    return False


def _file_uses_readincr_or_writeincr(content):
    """Check if file contains readincr or writeincr usage."""
    return 'readincr(' in content or 'writeincr(' in content


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue

        # We look for stream type parameters in files that also use readincr/writeincr
        # or in header files that declare kernel signatures (paired with source using readincr)
        has_stream_ops = _file_uses_readincr_or_writeincr(content)

        # Also check if any other file in the project uses readincr/writeincr
        # (header might declare the signature, source uses readincr)
        other_files_have_ops = any(
            _file_uses_readincr_or_writeincr(c)
            for fp, c in project_files.items()
            if fp != file_path and _is_kernel_file(fp)
        )

        if not (has_stream_ops or other_files_have_ops):
            continue

        # Find all stream type parameter declarations
        for match in _STREAM_PARAM_PATTERN.finditer(content):
            original_type = match.group(1)
            if original_type not in _STREAM_TYPE_REPLACEMENTS:
                continue

            replacement_type = _STREAM_TYPE_REPLACEMENTS[original_type]

            start = match.start(1)
            end = match.end(1)
            original_text = content[start:end]

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_type,
                "description": (
                    f"Change stream parameter type from '{original_type}' to "
                    f"'{replacement_type}' without updating readincr/writeincr usage, "
                    f"creating a stream type width mismatch."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]

    new_files[file_path] = new_content
    return new_files
