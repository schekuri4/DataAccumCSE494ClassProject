import re
import copy

BUG_FAMILY = {
    "family_id": "BF111",
    "bug_type": "readincr_v_wrong_vector_size",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<",
        "input_stream<",
        "input_stream_int32",
        "input_stream_cint16"
    ],
    "mutation_strategy": "Change the template parameter of readincr_v<N> to a value that does not match the stream's declared element width or the port's PLIO width. For example, change readincr_v<8>(s) to readincr_v<16>(s) when the stream is 32-bit PLIO with 128-bit width, causing a lane count mismatch.",
    "repair_expectation": "Restore the correct vector lane count N in readincr_v<N> so that N * element_bitwidth matches the stream's physical width (e.g., 4 for 32-bit elements on 128-bit stream).",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or static assertion about vector size mismatch in readincr_v.",
    "tags": [
        "lane_mismatch",
        "readincr_v",
        "stream_vector_interfaces",
        "stream_width",
        "vector_size"
    ]
}

# Common vector sizes used in AIE readincr_v
VALID_VECTOR_SIZES = [2, 4, 8, 16, 32]


def _is_kernel_source(file_path):
    """Heuristic to identify kernel source files (C/C++ for AIE)."""
    extensions = ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx')
    return file_path.lower().endswith(extensions)


def _pick_replacement_size(original_size):
    """Pick a different vector size that creates a mismatch."""
    # Double or halve the size, ensuring it's different
    candidates = [s for s in VALID_VECTOR_SIZES if s != original_size]
    if not candidates:
        # Fallback: just double it
        return original_size * 2
    # Prefer doubling if possible, otherwise halving
    doubled = original_size * 2
    halved = original_size // 2
    if doubled in candidates:
        return doubled
    if halved in candidates and halved > 0:
        return halved
    # Otherwise pick the first different candidate
    return candidates[0]


def find_mutation_candidates(project_files):
    """Find all readincr_v<N> calls in kernel source files."""
    candidates = []
    # Pattern matches readincr_v<N> where N is an integer
    pattern = re.compile(r'readincr_v\s*<\s*(\d+)\s*>')

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file has any stream-related content (match_targets)
        has_stream_context = any(
            target in content for target in BUG_FAMILY["match_targets"]
        )
        if not has_stream_context:
            continue

        for match in pattern.finditer(content):
            original_size = int(match.group(1))
            replacement_size = _pick_replacement_size(original_size)

            original_text = match.group(0)
            # Reconstruct replacement preserving spacing style
            replacement_text = re.sub(
                r'(readincr_v\s*<\s*)\d+(\s*>)',
                r'\g<1>' + str(replacement_size) + r'\g<2>',
                original_text
            )

            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed readincr_v vector size from {original_size} to "
                    f"{replacement_size}, causing a lane count mismatch with "
                    f"the stream's physical width."
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

    # Verify the original text is still at the expected location
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    else:
        new_content = content[:start] + candidate["replacement"] + content[end:]

    new_project_files[file_path] = new_content
    return new_project_files
