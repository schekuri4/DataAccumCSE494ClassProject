import re
import copy

BUG_FAMILY = {
    "family_id": "BF193",
    "bug_type": "begin_vector_invalid_element_size",
    "category": "vector_load_store",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::begin_vector<",
        "input_buffer",
        "output_buffer"
    ],
    "mutation_strategy": "Change the template parameter N in aie::begin_vector<N>(buf) to a value that does not evenly divide the AIE vector register width for the given element type. For example, use aie::begin_vector<7> or aie::begin_vector<3> with int32 data, which are not powers of two or valid AIE vector lengths.",
    "repair_expectation": "Replace the invalid vector length with a valid power-of-two length that is supported for the element type (e.g., 4, 8, 16, 32).",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert or template constraint violation on vector length.",
    "tags": [
        "begin_vector",
        "compile_error",
        "invalid_length",
        "static_assert",
        "vector_load_store"
    ]
}

# Valid AIE vector lengths (powers of two commonly supported)
VALID_VECTOR_LENGTHS = {2, 4, 8, 16, 32, 64, 128, 256}

# Invalid replacements to use - non-power-of-two values
INVALID_VECTOR_LENGTHS = [3, 5, 7, 9, 11, 13, 15]


def _is_kernel_source(file_path):
    """Heuristic to identify kernel source files."""
    extensions = ('.cpp', '.cc', '.cxx', '.c', '.h', '.hpp')
    return file_path.lower().endswith(extensions)


def _pick_invalid_replacement(original_value):
    """Pick an invalid vector length different from the original."""
    for val in INVALID_VECTOR_LENGTHS:
        if val != original_value:
            return val
    return 7  # fallback


def find_mutation_candidates(project_files):
    candidates = []
    # Pattern to match aie::begin_vector<N>(...), ::aie::begin_vector<N>(...),
    # and cbegin/begin_restrict variants where N is an integer.
    pattern = re.compile(
        r'((?:::)?aie::(?:c?begin|begin_restrict)_vector\s*<\s*)(\d+)(\s*>)'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file has any of the match targets (at least begin_vector)
        has_begin_vector = 'begin_vector' in content or 'begin_restrict_vector' in content
        if not has_begin_vector:
            continue

        for match in pattern.finditer(content):
            original_n_str = match.group(2)
            original_n = int(original_n_str)

            # Only mutate if the current value looks valid
            if original_n not in VALID_VECTOR_LENGTHS and original_n <= 0:
                continue

            invalid_n = _pick_invalid_replacement(original_n)
            original_text = match.group(0)
            replacement_text = match.group(1) + str(invalid_n) + match.group(3)

            start = match.start()
            end = match.end()

            candidate = {
                "file_path": file_path,
                "bug_type": "begin_vector_invalid_element_size",
                "category": "vector_load_store",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed aie::begin_vector<{original_n}> to "
                    f"aie::begin_vector<{invalid_n}> which is not a valid "
                    f"AIE vector length (not a power of two), causing a "
                    f"compile-time static_assert or template constraint failure."
                )
            }
            candidates.append(candidate)

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
