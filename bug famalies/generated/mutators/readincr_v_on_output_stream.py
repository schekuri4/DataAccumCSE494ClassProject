import re
import copy

BUG_FAMILY = {
    "family_id": "BF115",
    "bug_type": "readincr_v_on_output_stream",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["readincr_v<", "output_stream<", "writeincr_v<"],
    "mutation_strategy": "Call readincr_v<N>() on an output_stream pointer, or call writeincr_v() on an input_stream pointer, reversing the read/write operation relative to the stream direction.",
    "repair_expectation": "Use readincr_v on input_stream pointers and writeincr_v on output_stream pointers, matching the operation to the stream direction.",
    "validation_signal": "WSL Vitis/AIE compile failure with no matching overload for readincr_v with output_stream* argument or vice versa.",
    "tags": ["api_misuse", "direction_mismatch", "readincr_v", "stream_vector_interfaces", "writeincr_v"]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx'))


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern 1: readincr_v<...>(...) called with an input_stream argument
    # Mutate to: keep readincr_v but it will be applied where writeincr_v should be,
    # OR swap readincr_v -> writeincr_v (direction mismatch)
    
    # Strategy: Find readincr_v calls and replace with writeincr_v (making it a write on input_stream)
    # OR find writeincr_v calls and replace with readincr_v (making it a read on output_stream)

    # Pattern for readincr_v<N>(stream_ptr) - replace with writeincr_v
    readincr_pattern = re.compile(
        r'(readincr_v<(\d+)>\s*\(\s*)([^,\)]+)(\s*\))'
    )
    
    # Pattern for writeincr_v<N>(stream_ptr, data) - replace with readincr_v
    writeincr_pattern = re.compile(
        r'(writeincr_v<(\d+)>\s*\(\s*)([^,]+)(,[^)]+\))'
    )

    # More general patterns that capture the full call
    # readincr_v<N>(...)
    readincr_full = re.compile(r'readincr_v<\s*\d+\s*>\s*\([^)]*\)')
    # writeincr_v<N>(...)
    writeincr_full = re.compile(r'writeincr_v<\s*\d+\s*>\s*\([^)]*\)')

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Find readincr_v calls and mutate to writeincr_v (direction mismatch)
        for match in readincr_full.finditer(content):
            original = match.group(0)
            # Replace readincr_v with writeincr_v - this creates a writeincr_v on input_stream
            replacement = original.replace('readincr_v', 'writeincr_v', 1)
            if replacement != original:
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "readincr_v_on_output_stream",
                    "category": "stream_vector_interfaces",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Replace readincr_v with writeincr_v, causing a write operation "
                        f"on an input_stream pointer (direction mismatch)."
                    )
                })

        # Find writeincr_v calls and mutate to readincr_v (direction mismatch)
        for match in writeincr_full.finditer(content):
            original = match.group(0)
            # Replace writeincr_v with readincr_v - this creates a readincr_v on output_stream
            # Also need to remove the data argument since readincr_v only takes stream ptr
            replacement_base = original.replace('writeincr_v', 'readincr_v', 1)
            # Try to remove second argument: readincr_v<N>(stream_ptr, data) -> readincr_v<N>(stream_ptr)
            arg_fix = re.compile(r'(readincr_v<\s*\d+\s*>\s*\([^,]+),\s*[^)]+(\))')
            fixed = arg_fix.sub(r'\1\2', replacement_base)
            if fixed != original:
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "readincr_v_on_output_stream",
                    "category": "stream_vector_interfaces",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": fixed,
                    "description": (
                        f"Replace writeincr_v with readincr_v, causing a read operation "
                        f"on an output_stream pointer (direction mismatch)."
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

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
