import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF194",
    "bug_type": "begin_restrict_vector_const_qualifier_mismatch",
    "category": "vector_load_store",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::begin_restrict_vector<",
        "aie::begin_vector<",
        "aie::cbegin_vector<",
        "output_buffer",
        "input_buffer"
    ],
    "mutation_strategy": "Use aie::begin_restrict_vector on an output_buffer with a const-qualified iterator expectation, or attempt to write through a restrict vector iterator obtained from an input_buffer (read-only). This creates a const-correctness violation at compile time.",
    "repair_expectation": "Use the correct buffer direction: begin_restrict_vector on input_buffer for reading, on output_buffer for writing, matching const qualifiers.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about assigning to read-only or const iterator dereference.",
    "tags": [
        "begin_restrict_vector",
        "compile_error",
        "const_qualifier",
        "input_output",
        "vector_load_store"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find sites where begin_restrict_vector is used with input_buffer or output_buffer,
    and propose swapping the buffer direction to create a const-qualifier mismatch."""
    candidates: list[dict[str, object]] = []

    # Pattern 1: aie::begin_restrict_vector<...>( input_buffer_var )
    # Mutation: swap input_buffer reference to output_buffer or vice versa
    # More practically: find lines with begin_restrict_vector used on input_buffer
    # and swap to output_buffer, or vice versa.

    # We look for patterns like:
    #   aie::begin_restrict_vector<TYPE>(input_...)  -> swap to output_...
    #   aie::begin_restrict_vector<TYPE>(output_...) -> swap to input_...

    # Pattern to match aie::begin_restrict_vector<...>(some_buffer_name)
    restrict_vec_pattern = re.compile(
        r'(aie::begin_restrict_vector\s*<[^>]+>\s*\(\s*)'
        r'(input_buffer|output_buffer)'
        r'(\b[^)]*\))'
    )

    # Also match cases where the buffer variable name contains "input" or "output"
    # but isn't literally "input_buffer" / "output_buffer"
    general_pattern = re.compile(
        r'(aie::begin_restrict_vector\s*<[^>]+>\s*\(\s*)'
        r'(\w*(?:input|output)\w*)'
        r'(\s*[^)]*\))'
    )

    begin_vector_pattern = re.compile(
        r'((?:::)?aie::)(begin_vector|cbegin_vector)(\s*<[^>]+>\s*\(\s*)'
        r'(\w*(?:out|output|in|input)\w*)'
        r'(\s*\))'
    )

    kernel_extensions = ('.cc', '.cpp', '.h', '.hpp', '.c', '.cxx')

    for file_path, content in project_files.items():
        # Only consider kernel source files
        if not any(file_path.endswith(ext) for ext in kernel_extensions):
            continue

        # Check if file has relevant content
        if 'begin_restrict_vector' not in content and 'begin_vector' not in content:
            continue

        lines = content.split('\n')
        offset = 0
        for line_idx, line in enumerate(lines):
            # Try the specific pattern first
            for match in restrict_vec_pattern.finditer(line):
                buf_name = match.group(2)
                if buf_name == "input_buffer":
                    new_buf = "output_buffer"
                    desc = ("Replace input_buffer with output_buffer in "
                            "aie::begin_restrict_vector call, causing a const-qualifier "
                            "mismatch when writing through a read-only iterator.")
                else:
                    new_buf = "input_buffer"
                    desc = ("Replace output_buffer with input_buffer in "
                            "aie::begin_restrict_vector call, causing a const-qualifier "
                            "mismatch when trying to write through a const iterator.")

                full_match = match.group(0)
                replacement = match.group(1) + new_buf + match.group(3)
                start = offset + match.start()
                end = offset + match.end()

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "begin_restrict_vector_const_qualifier_mismatch",
                    "category": "vector_load_store",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": desc
                })

            # If no specific match, try general pattern
            if not restrict_vec_pattern.search(line):
                for match in general_pattern.finditer(line):
                    buf_name = match.group(2)
                    if 'input' in buf_name.lower():
                        new_buf = buf_name.replace('input', 'output').replace('Input', 'Output')
                        desc = ("Replace input buffer variable with output equivalent in "
                                "aie::begin_restrict_vector, creating const-qualifier mismatch.")
                    elif 'output' in buf_name.lower():
                        new_buf = buf_name.replace('output', 'input').replace('Output', 'Input')
                        desc = ("Replace output buffer variable with input equivalent in "
                                "aie::begin_restrict_vector, creating const-qualifier mismatch.")
                    else:
                        continue

                    # Skip if replacement is same as original
                    if new_buf == buf_name:
                        continue

                    full_match = match.group(0)
                    replacement = match.group(1) + new_buf + match.group(3)
                    start = offset + match.start()
                    end = offset + match.end()

                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "begin_restrict_vector_const_qualifier_mismatch",
                        "category": "vector_load_store",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement,
                        "description": desc
                    })

            for match in begin_vector_pattern.finditer(line):
                func = match.group(2)
                buf_name = match.group(4)
                lower_buf = buf_name.lower()
                if func == "begin_vector" and ("out" in lower_buf or "output" in lower_buf):
                    replacement_func = "cbegin_vector"
                    desc = (
                        "Use cbegin_vector on an output buffer, so writes through "
                        "the iterator hit a const-qualifier compile error."
                    )
                elif func == "cbegin_vector" and ("in" in lower_buf or "input" in lower_buf):
                    replacement_func = "begin_vector"
                    desc = (
                        "Use a mutable vector iterator on an input buffer, creating "
                        "a read-only buffer constness mismatch."
                    )
                else:
                    continue

                full_match = match.group(0)
                replacement = (
                    match.group(1) + replacement_func + match.group(3) +
                    buf_name + match.group(5)
                )
                start = offset + match.start()
                end = offset + match.end()
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "begin_restrict_vector_const_qualifier_mismatch",
                    "category": "vector_load_store",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": desc
                })

            offset += len(line) + 1  # +1 for newline

    # Deduplicate candidates by (file_path, start, end)
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c["file_path"], c["start"], c["end"])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    return unique_candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate to the project files, returning a new dict."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    if file_path not in new_files:
        return new_files

    content = new_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
