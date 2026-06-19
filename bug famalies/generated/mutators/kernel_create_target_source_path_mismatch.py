import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF047",
    "bug_type": "kernel_create_target_source_path_mismatch",
    "category": "kernel_source_paths",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "kernel::create(",
        "adf::source(",
        "source(k_"
    ],
    "mutation_strategy": "Assign adf::source() to a kernel object with a path that contains a valid file but whose function signature does not match the template argument in kernel::create<function_name>(), causing a mismatch between the declared kernel entry point and the source file contents.",
    "repair_expectation": "Ensure the adf::source() path points to the file that actually defines the function specified in kernel::create<>().",
    "validation_signal": "WSL Vitis/AIE compile failure with undefined reference or function not found in the specified source file.",
    "tags": [
        "adf_source",
        "function_mismatch",
        "kernel_create",
        "kernel_source_paths",
        "linker_error"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find adf::source() calls and propose swapping their paths to create mismatches."""
    candidates: list[dict[str, object]] = []

    # Pattern to match source assignments like:
    # adf::source(k_name) = "path/to/file.cc";
    # source(k_name) = "path/to/file.cc";
    source_pattern = re.compile(
        r'((?:adf::)?source\s*\(\s*([^)]+?)\s*\)\s*=\s*"([^"]+)"\s*;)'
    )

    # Pattern to match kernel::create calls like:
    # k_name = kernel::create<func_name>(...)
    # k_name = adf::kernel::create<func_name>(...)
    create_pattern = re.compile(
        r'(\w+)\s*=\s*(?:adf::)?kernel::create\s*<\s*(\w+)\s*>'
    )

    # Look through all files that could be graph headers or sources
    target_extensions = ('.h', '.hpp', '.cpp', '.cc', '.cxx', '.hxx')

    for file_path, content in project_files.items():
        if not file_path.endswith(target_extensions):
            continue

        # Check if file contains relevant patterns
        has_source = "source(" in content
        has_create = "kernel::create(" in content or "kernel::create<" in content

        if not (has_source and has_create):
            continue

        # Collect all source assignments in this file
        source_matches = list(source_pattern.finditer(content))
        # Collect all kernel::create mappings
        create_matches = list(create_pattern.finditer(content))

        if len(source_matches) < 2 and len(source_matches) < 1:
            continue

        # Build mapping from kernel variable to its create function
        kernel_func_map: dict[str, str] = {}
        for cm in create_matches:
            kernel_var = cm.group(1)
            func_name = cm.group(2)
            kernel_func_map[kernel_var] = func_name

        # If we have multiple source assignments, we can swap paths between them
        if len(source_matches) >= 2:
            for i in range(len(source_matches)):
                for j in range(len(source_matches)):
                    if i == j:
                        continue
                    sm_i = source_matches[i]
                    sm_j = source_matches[j]

                    kernel_var_i = sm_i.group(2)
                    path_i = sm_i.group(3)
                    path_j = sm_j.group(3)

                    # Only mutate if paths are different
                    if path_i == path_j:
                        continue

                    func_name = kernel_func_map.get(kernel_var_i, kernel_var_i)

                    # Build the original full match and replacement
                    original_line = sm_i.group(1)
                    replacement_line = original_line.replace(
                        f'"{path_i}"', f'"{path_j}"'
                    )

                    start = sm_i.start()
                    end = sm_i.end()

                    description = (
                        f"Replace source path for kernel '{kernel_var_i}' "
                        f"(create<{func_name}>) from \"{path_i}\" to \"{path_j}\", "
                        f"causing a mismatch between the kernel entry point and source file."
                    )

                    candidates.append({
                        "file_path": file_path,
                        "bug_type": BUG_FAMILY["bug_type"],
                        "category": BUG_FAMILY["category"],
                        "start": start,
                        "end": end,
                        "original": original_line,
                        "replacement": replacement_line,
                        "description": description,
                    })

        # If only one source assignment, try modifying the path to point to a different
        # kernel source file that exists in the project
        elif len(source_matches) == 1:
            sm = source_matches[0]
            kernel_var = sm.group(2)
            current_path = sm.group(3)

            func_name = kernel_func_map.get(kernel_var, kernel_var)

            # Find other kernel source files in the project
            other_kernel_files = []
            for pf_path in project_files:
                if pf_path.endswith(('.cc', '.cpp', '.cxx')):
                    # Check it's not the same file referenced
                    if not pf_path.endswith(current_path) and current_path not in pf_path:
                        other_kernel_files.append(pf_path)

            for other_file in other_kernel_files:
                original_line = sm.group(1)
                replacement_line = original_line.replace(
                    f'"{current_path}"', f'"{other_file}"'
                )

                description = (
                    f"Replace source path for kernel '{kernel_var}' "
                    f"(create<{func_name}>) from \"{current_path}\" to \"{other_file}\", "
                    f"causing a mismatch between the kernel entry point and source file."
                )

                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": sm.start(),
                    "end": sm.end(),
                    "original": original_line,
                    "replacement": replacement_line,
                    "description": description,
                })
                # Only generate one candidate per source match for single-source case
                break

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply the mutation to produce a new set of project files."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    content = new_project_files[file_path]

    # Replace at the exact position
    start = candidate["start"]
    end = candidate["end"]

    # Verify the content at the expected position matches
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
