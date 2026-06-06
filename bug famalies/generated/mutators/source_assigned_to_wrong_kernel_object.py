import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF049",
    "bug_type": "source_assigned_to_wrong_kernel_object",
    "category": "kernel_source_paths",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["adf::source(", "source(k_", "kernel::create"],
    "mutation_strategy": "Swap the adf::source() assignments between two kernel objects in the graph, so kernel A gets kernel B's source file and vice versa. This causes type/signature mismatches when the compiler tries to resolve the kernel entry points.",
    "repair_expectation": "Reassign each adf::source() call to its correct corresponding kernel object.",
    "validation_signal": "WSL Vitis/AIE compile failure with function signature mismatch or undefined kernel entry point errors.",
    "tags": [
        "adf_source",
        "kernel_source_paths",
        "signature_mismatch",
        "swapped_assignment",
        "wrong_kernel",
    ],
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for graph files
    if "graph" in lower:
        return True
    # Also check extensions that could be graph files
    if lower.endswith((".h", ".hpp", ".cpp", ".cc")):
        return True
    return False


def _find_source_assignments(content):
    """Find all adf::source() or source() assignment statements."""
    # Match patterns like:
    #   adf::source(kernel_obj) = "path/to/file.cc";
    #   source(kernel_obj) = "path/to/file.cc";
    pattern = re.compile(
        r'((?:adf::)?source\s*\(\s*(\w+)\s*\)\s*=\s*"([^"]+)"\s*;)',
        re.MULTILINE
    )
    matches = []
    for m in pattern.finditer(content):
        matches.append({
            "full_match": m.group(1),
            "kernel_obj": m.group(2),
            "source_path": m.group(3),
            "start": m.start(),
            "end": m.end(),
        })
    return matches


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Check if file contains any of the match targets
        has_match_target = any(target in content for target in BUG_FAMILY["match_targets"])
        if not has_match_target:
            continue

        source_assignments = _find_source_assignments(content)

        # We need at least 2 source assignments to swap
        if len(source_assignments) < 2:
            continue

        # Generate swap candidates for each pair
        for i in range(len(source_assignments)):
            for j in range(i + 1, len(source_assignments)):
                a = source_assignments[i]
                b = source_assignments[j]

                # Only swap if they have different source paths (otherwise no-op)
                if a["source_path"] == b["source_path"]:
                    continue

                # Build the swapped versions
                # Replace kernel A's source with kernel B's source path and vice versa
                new_a = a["full_match"].replace(
                    '"{}"'.format(a["source_path"]),
                    '"{}"'.format(b["source_path"])
                )
                new_b = b["full_match"].replace(
                    '"{}"'.format(b["source_path"]),
                    '"{}"'.format(a["source_path"])
                )

                # Original text is both statements in their original positions
                # We describe the mutation as a swap
                description = (
                    f"Swap adf::source() assignments: "
                    f"{a['kernel_obj']} (was \"{a['source_path']}\") gets \"{b['source_path']}\" "
                    f"and {b['kernel_obj']} (was \"{b['source_path']}\") gets \"{a['source_path']}\""
                )

                candidate = {
                    "file_path": filepath,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": a["start"],
                    "end": b["end"],
                    "original": (a["full_match"], b["full_match"]),
                    "replacement": (new_a, new_b),
                    "description": description,
                    # Extra info for apply_mutation
                    "_swap_a": {"start": a["start"], "end": a["end"], "original": a["full_match"], "replacement": new_a},
                    "_swap_b": {"start": b["start"], "end": b["end"], "original": b["full_match"], "replacement": new_b},
                }
                candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]

    swap_a = candidate["_swap_a"]
    swap_b = candidate["_swap_b"]

    # Apply replacements from end to start to preserve indices
    # swap_b comes after swap_a (by construction: i < j means a.start < b.start)
    new_content = (
        content[:swap_a["start"]] +
        swap_a["replacement"] +
        content[swap_a["end"]:swap_b["start"]] +
        swap_b["replacement"] +
        content[swap_b["end"]:]
    )

    new_files[filepath] = new_content
    return new_files
