import re
import copy

BUG_FAMILY = {
    "family_id": "BF195",
    "bug_type": "extract_index_out_of_range",
    "category": "vector_load_store",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [".extract<", "aie::vector<", ".insert("],
    "mutation_strategy": "Change the index argument in vector.extract<SubVecSize>(idx) to a value that exceeds the number of sub-vectors. For example, on an aie::vector<int32,16>, call .extract<8>(2) which requires index < 2, or on aie::vector<int32,8> call .extract<4>(3) which requires index < 2.",
    "repair_expectation": "Correct the extract index to be within [0, N/SubVecSize - 1] for the given parent vector size and sub-vector size.",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert on extract index bounds or template constraint error.",
    "tags": ["compile_error", "extract", "index_bounds", "sub_vector", "vector_load_store"]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def _find_vector_declarations(content):
    """Find aie::vector declarations and return dict mapping variable name to (element_type, vector_size)."""
    # Match patterns like: aie::vector<int32, 16> varname
    # Also handles aie::vector<int32,16> and with various whitespace
    pattern = re.compile(
        r'aie::vector\s*<\s*(\w+)\s*,\s*(\d+)\s*>\s+(\w+)'
    )
    declarations = {}
    for m in pattern.finditer(content):
        elem_type = m.group(1)
        vec_size = int(m.group(2))
        var_name = m.group(3)
        declarations[var_name] = (elem_type, vec_size)
    return declarations


def _compute_out_of_range_index(parent_size, sub_vec_size):
    """Compute an index that is out of range for extract."""
    if sub_vec_size <= 0 or parent_size <= 0:
        return None
    max_valid_index = (parent_size // sub_vec_size) - 1
    if max_valid_index < 0:
        return None
    # Return an index that is one beyond the valid range
    return max_valid_index + 1


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Find vector declarations to know sizes
        declarations = _find_vector_declarations(content)

        # Find .extract<SubVecSize>(index) calls
        # Pattern: varname.extract<number>(number)
        extract_pattern = re.compile(
            r'(\w+)\s*\.\s*extract\s*<\s*(\d+)\s*>\s*\(\s*(\d+)\s*\)'
        )

        for m in extract_pattern.finditer(content):
            var_name = m.group(1)
            sub_vec_size = int(m.group(2))
            current_index = int(m.group(3))

            # Try to find the parent vector size
            parent_size = None
            if var_name in declarations:
                _, parent_size = declarations[var_name]
            else:
                # If we can't find the declaration, try to infer from context
                # Look for common sizes; skip if we can't determine
                continue

            if parent_size is None or sub_vec_size <= 0:
                continue

            max_valid_index = (parent_size // sub_vec_size) - 1
            if max_valid_index < 0:
                continue

            # Only mutate if current index is valid (we want to introduce a bug)
            if current_index > max_valid_index:
                continue  # Already out of range

            out_of_range_index = _compute_out_of_range_index(parent_size, sub_vec_size)
            if out_of_range_index is None:
                continue

            # If the out_of_range_index equals current_index, bump further
            if out_of_range_index == current_index:
                out_of_range_index += 1

            original_text = m.group(0)
            replacement_text = f"{var_name}.extract<{sub_vec_size}>({out_of_range_index})"

            start = m.start()
            end = m.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "extract_index_out_of_range",
                "category": "vector_load_store",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed extract index from {current_index} to {out_of_range_index} "
                    f"on vector '{var_name}' (size={parent_size}, sub_vec_size={sub_vec_size}, "
                    f"max valid index={max_valid_index}), causing an out-of-range extract."
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

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
