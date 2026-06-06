import re
import os
from typing import Any


BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF044",
    "bug_type": "missing_kernel_source_file_reference",
    "category": "kernel_source_paths",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "reference_missing_file",
    "match_targets": [
        "adf::source(",
        "source(k_",
        "kernel::create"
    ],
    "mutation_strategy": "Add an adf::source() call that references a kernel source file that does not exist in the project (e.g., 'src/kernels/filter_v2.cc' when only 'filter.cc' exists), simulating a rename that was not propagated to the graph.",
    "repair_expectation": "Either create the missing file or update the adf::source() path to reference the correct existing file.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating the referenced kernel source file cannot be found.",
    "tags": [
        "adf_source",
        "kernel_source_paths",
        "missing_file",
        "reference_error",
        "rename_propagation"
    ]
}


def _is_graph_file(file_path: str) -> bool:
    """Heuristic to identify graph header or source files."""
    basename = os.path.basename(file_path).lower()
    # Common patterns for graph files in AIE projects
    if 'graph' in basename:
        return True
    # Also consider .h/.hpp/.cpp/.cc files that might contain graph definitions
    ext = os.path.splitext(file_path)[1].lower()
    return ext in ('.h', '.hpp', '.cpp', '.cc', '.c')


def _file_contains_graph_indicators(content: str) -> bool:
    """Check if file content has indicators of being a graph file."""
    indicators = ['adf::graph', 'class.*graph', 'adf::source', 'kernel::create',
                  '#include.*adf.h', '#include.*adf/stream']
    for pattern in indicators:
        if re.search(pattern, content):
            return True
    return False


def _generate_nonexistent_path(original_path: str, project_files: dict[str, str]) -> str:
    """Generate a path that doesn't exist in the project based on the original."""
    dirname = os.path.dirname(original_path)
    basename = os.path.basename(original_path)
    name, ext = os.path.splitext(basename)

    # Try common rename patterns
    candidates = [
        os.path.join(dirname, name + "_v2" + ext),
        os.path.join(dirname, name + "_new" + ext),
        os.path.join(dirname, name + "_updated" + ext),
        os.path.join(dirname, name + "2" + ext),
    ]

    # Normalize for comparison
    all_files_normalized = set()
    for fp in project_files:
        all_files_normalized.add(os.path.normpath(fp))

    for candidate in candidates:
        normalized = os.path.normpath(candidate)
        if normalized not in all_files_normalized:
            return candidate

    # Fallback: just append _missing
    return os.path.join(dirname, name + "_missing" + ext)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match adf::source() or source() calls with a string path
    source_pattern = re.compile(
        r'((?:adf::)?source\s*\(\s*\w+\s*\)\s*=\s*"([^"]+)")'
    )
    # Alternative pattern: adf::source(kernel_var) = "path";
    # Also: source(k_xxx) = "path";

    # More general pattern for source assignments
    source_assign_pattern = re.compile(
        r'((?:adf::)?source\s*\(\s*[\w\.]+\s*\)\s*=\s*"([^"]+)")'
    )

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        if not _file_contains_graph_indicators(content):
            continue

        # Find all adf::source() assignments
        for match in source_assign_pattern.finditer(content):
            full_match = match.group(1)
            referenced_path = match.group(2)
            start = match.start()
            end = match.end()

            # Generate a non-existent file path
            nonexistent_path = _generate_nonexistent_path(referenced_path, project_files)

            # Create the replacement: change the path in the source() call
            replacement = full_match.replace(referenced_path, nonexistent_path)

            candidates.append({
                "file_path": file_path,
                "bug_type": "missing_kernel_source_file_reference",
                "category": "kernel_source_paths",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": (
                    f"Changed kernel source path from '{referenced_path}' to "
                    f"'{nonexistent_path}' (non-existent file), simulating a "
                    f"rename that was not propagated to the graph."
                )
            })

    # If no source() assignments found, look for files with kernel::create
    # and try to add a new adf::source() line referencing a non-existent file
    if not candidates:
        kernel_create_pattern = re.compile(
            r'((\w+)\s*=\s*(?:adf::)?kernel::create\s*<\s*(\w+)\s*>\s*\(\s*[^)]*\)\s*;)'
        )

        for file_path, content in project_files.items():
            if not _is_graph_file(file_path):
                continue
            if not _file_contains_graph_indicators(content):
                continue

            for match in kernel_create_pattern.finditer(content):
                full_match = match.group(1)
                kernel_var = match.group(2)
                kernel_func = match.group(3)
                end_pos = match.end()

                # Generate a fake source path
                fake_path = f"src/kernels/{kernel_func}_v2.cc"

                # Make sure it doesn't exist
                if any(os.path.normpath(fp) == os.path.normpath(fake_path)
                       for fp in project_files):
                    fake_path = f"src/kernels/{kernel_func}_missing.cc"

                # The mutation: append a source() line after the kernel::create line
                new_source_line = f'\n    adf::source({kernel_var}) = "{fake_path}";'
                replacement = full_match + new_source_line

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "missing_kernel_source_file_reference",
                    "category": "kernel_source_paths",
                    "start": match.start(),
                    "end": end_pos,
                    "original": full_match,
                    "replacement": replacement,
                    "description": (
                        f"Added adf::source({kernel_var}) referencing non-existent "
                        f"file '{fake_path}' after kernel::create, simulating a "
                        f"rename that was not propagated to the graph."
                    )
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply the mutation to produce a new set of project files."""
    new_project_files = dict(project_files)

    file_path = candidate["file_path"]
    original_content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if original_content[start:end] == original:
        new_content = original_content[:start] + replacement + original_content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = original_content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
