import re
import copy


BUG_FAMILY = {
    "family_id": "BF041",
    "bug_type": "wrong_kernel_source_relative_path",
    "category": "kernel_source_paths",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::source(",
        "source(k_",
        "\"src/kernels/",
        "\"kernels/"
    ],
    "mutation_strategy": "Modify the string argument in adf::source() to prepend or remove a directory level (e.g., change 'src/kernels/fir.cc' to 'kernels/fir.cc' or 'src/src/kernels/fir.cc'), making the relative path incorrect from the project root.",
    "repair_expectation": "Restore the correct relative path that matches the actual filesystem location of the kernel source file.",
    "validation_signal": "WSL Vitis/AIE compile failure with error indicating kernel source file not found or cannot open source file.",
    "tags": [
        "adf_source",
        "file_not_found",
        "graph_definition",
        "kernel_source_paths",
        "relative_path"
    ]
}


# Pattern to match adf::source() or source() calls with a string argument containing a path
_SOURCE_CALL_PATTERN = re.compile(
    r'((?:adf::)?source\s*\(\s*\w+\s*,\s*)"([^"]+)"(\s*\))',
    re.MULTILINE
)


def _is_graph_file(file_path):
    """Heuristic to identify graph header or graph source files."""
    lower = file_path.lower()
    # Common patterns for graph files in AIE projects
    if 'graph' in lower:
        return True
    # Also check for .h/.hpp/.cpp files that might contain adf::source calls
    extensions = ('.h', '.hpp', '.cpp', '.cc', '.cxx')
    return any(lower.endswith(ext) for ext in extensions)


def _mutate_path(original_path):
    """Generate a mutated path by either removing or adding a directory level."""
    # If path starts with "src/kernels/", remove "src/" prefix
    if original_path.startswith("src/kernels/"):
        return "kernels/" + original_path[len("src/kernels/"):]
    # If path starts with "src/", remove "src/" prefix
    elif original_path.startswith("src/"):
        return original_path[len("src/"):]
    # If path starts with "kernels/", prepend "src/"
    elif original_path.startswith("kernels/"):
        return "src/" + original_path
    # Otherwise, prepend "src/" to create an incorrect path
    else:
        return "src/" + original_path


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Check if file contains any of the match targets
        has_match_target = any(target in content for target in BUG_FAMILY["match_targets"])
        if not has_match_target:
            continue

        for match in _SOURCE_CALL_PATTERN.finditer(content):
            original_path = match.group(2)

            # Only mutate paths that look like kernel source paths
            if not (original_path.endswith('.cc') or original_path.endswith('.cpp') or
                    original_path.endswith('.c') or original_path.endswith('.h') or
                    original_path.endswith('.hpp')):
                continue

            mutated_path = _mutate_path(original_path)

            # Skip if mutation produces the same path
            if mutated_path == original_path:
                continue

            # The full original text is the entire match
            full_original = match.group(0)
            full_replacement = match.group(1) + '"' + mutated_path + '"' + match.group(3)

            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": full_original,
                "replacement": full_replacement,
                "description": (
                    f"Changed kernel source path from \"{original_path}\" to "
                    f"\"{mutated_path}\" in adf::source() call, making the "
                    f"relative path incorrect."
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
