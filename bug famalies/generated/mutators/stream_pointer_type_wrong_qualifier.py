import re
import copy

BUG_FAMILY = {
    "family_id": "BF114",
    "bug_type": "stream_pointer_type_wrong_qualifier",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_stream<",
        "output_stream<",
        "input_stream_int32*",
        "output_stream_cint16*"
    ],
    "mutation_strategy": "Change the stream pointer type in the kernel function signature from input_stream<T>* to output_stream<T>* or vice versa, creating a direction mismatch between the kernel signature and the graph's connect<stream> topology.",
    "repair_expectation": "Restore the correct stream direction qualifier (input_stream vs output_stream) in the kernel function signature to match the graph connection direction.",
    "validation_signal": "WSL Vitis/AIE compile failure with kernel signature mismatch or port binding error during graph compilation.",
    "tags": ["kernel_signature", "pointer_type", "stream_direction", "stream_vector_interfaces"]
}


# Pattern to match input_stream or output_stream with optional template parameter
# Covers: input_stream<T>*, input_stream_int32*, output_stream<T>*, output_stream_cint16*, etc.
_STREAM_PATTERN = re.compile(
    r'\b(input_stream|output_stream)(\s*<[^>]*>\s*\*|_\w+\s*\*)'
)


def _is_kernel_file(filepath):
    """Heuristic: kernel source or header files typically have .cpp, .cc, .h, .hpp extensions."""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh'))


def _swap_direction(direction):
    """Swap input_stream <-> output_stream."""
    if direction == "input_stream":
        return "output_stream"
    else:
        return "input_stream"


def find_mutation_candidates(project_files):
    candidates = []
    
    for filepath, content in project_files.items():
        if not _is_kernel_file(filepath):
            continue
        
        for match in _STREAM_PATTERN.finditer(content):
            direction = match.group(1)
            suffix = match.group(2)
            
            original_text = match.group(0)
            swapped_direction = _swap_direction(direction)
            replacement_text = swapped_direction + suffix
            
            start = match.start()
            end = match.end()
            
            candidate = {
                "file_path": filepath,
                "bug_type": "stream_pointer_type_wrong_qualifier",
                "category": "stream_vector_interfaces",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed '{original_text}' to '{replacement_text}' in {filepath} "
                    f"at position {start}-{end}, creating a stream direction mismatch."
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
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text != original:
        # Fallback: try to find and replace the first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]
    
    new_files[filepath] = new_content
    return new_files
