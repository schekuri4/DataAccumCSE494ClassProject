import re
import copy

BUG_FAMILY = {
    "family_id": "BF061",
    "bug_type": "kernel_array_out_of_range_index",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["kernel k[", "kernel::create", "k[N]"],
    "mutation_strategy": "In the graph class, declare a kernel array of size N (e.g., kernel k[4]) but then reference an index >= N in connect<> statements, net<> declarations, or source/runtime_ratio assignments (e.g., k[4] when array is size 4).",
    "repair_expectation": "Change the out-of-range index to a valid index within [0, N-1] or increase the kernel array size to accommodate the referenced index.",
    "validation_signal": "WSL Vitis/AIE compile failure with array subscript out of range or segfault during graph elaboration.",
    "tags": ["endpoint_index", "graph_constructor", "graph_endpoint_indices", "kernel_array", "out_of_range"]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    return any(ext in lower for ext in ['.h', '.hpp', '.cpp', '.cc']) and ('graph' in lower or True)


def _find_kernel_arrays(content):
    """Find kernel array declarations like 'kernel k[4]' and return (name, size, match)."""
    # Match patterns like: kernel name[N] or kernel name [ N ]
    pattern = re.compile(r'\bkernel\s+(\w+)\s*\[\s*(\d+)\s*\]')
    results = []
    for m in pattern.finditer(content):
        name = m.group(1)
        size = int(m.group(2))
        results.append((name, size, m))
    return results


def _find_kernel_index_usages(content, array_name, array_size):
    """Find all usages of array_name[index] where index is a valid integer < array_size."""
    # Match usages like k[0], k[1], etc. but not the declaration itself
    pattern = re.compile(r'\b' + re.escape(array_name) + r'\s*\[\s*(\d+)\s*\]')
    usages = []
    for m in pattern.finditer(content):
        idx = int(m.group(1))
        # Only consider valid indices that we can mutate to out-of-range
        if 0 <= idx < array_size:
            usages.append(m)
    return usages


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        kernel_arrays = _find_kernel_arrays(content)
        if not kernel_arrays:
            continue

        for array_name, array_size, decl_match in kernel_arrays:
            if array_size < 1:
                continue

            usages = _find_kernel_index_usages(content, array_name, array_size)

            # Skip usages that overlap with the declaration itself
            decl_start = decl_match.start()
            decl_end = decl_match.end()

            for usage in usages:
                # Skip if this usage IS the declaration
                if usage.start() >= decl_start and usage.end() <= decl_end:
                    continue

                original_idx = int(usage.group(1))
                # Replace with an out-of-range index (use array_size itself, which is one past the end)
                out_of_range_idx = array_size

                original_text = usage.group(0)
                # Reconstruct with out-of-range index
                replacement_text = re.sub(
                    r'\[\s*' + str(original_idx) + r'\s*\]',
                    '[' + str(out_of_range_idx) + ']',
                    original_text
                )

                candidate = {
                    "file_path": filepath,
                    "bug_type": "kernel_array_out_of_range_index",
                    "category": "graph_endpoint_indices",
                    "start": usage.start(),
                    "end": usage.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Changed '{array_name}[{original_idx}]' to '{array_name}[{out_of_range_idx}]' "
                        f"which is out of range for array of size {array_size} "
                        f"(valid indices: 0 to {array_size - 1})."
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
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
