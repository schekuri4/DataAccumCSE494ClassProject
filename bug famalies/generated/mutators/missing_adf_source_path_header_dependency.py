import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF008",
    "bug_type": "missing_adf_source_path_header_dependency",
    "category": "include_headers",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["adf::source(", "kernel::create", "#include \""],
    "mutation_strategy": (
        "In the graph constructor, set adf::source() to point to a kernel .cc file, "
        "but remove or misname the #include of that kernel's corresponding .h header "
        "in the graph header, so kernel::create references an undeclared function "
        "prototype even though the source path is correct."
    ),
    "repair_expectation": "Add the correct #include for the kernel header file that declares the function used in kernel::create.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'use of undeclared identifier' or 'was not declared in this scope' for the kernel function name.",
    "tags": ["adf_source", "header_dependency", "include_headers", "kernel_create", "missing_declaration"],
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find graph header files that #include kernel headers corresponding to adf::source() paths."""
    candidates: list[dict[str, object]] = []

    # First, find files that contain adf::source() calls to identify kernel source files
    # Then find graph headers that include the corresponding kernel headers

    # Collect all files that have adf::source() and kernel::create (graph files)
    graph_files: list[str] = []
    for fpath, content in project_files.items():
        if "adf::source(" in content or "source(" in content:
            if "kernel::create" in content or "adf::kernel::create" in content:
                graph_files.append(fpath)

    # Also check for graph headers that include kernel headers
    header_files: list[str] = []
    for fpath, content in project_files.items():
        if fpath.endswith((".h", ".hpp")):
            if "#include \"" in content:
                if "kernel::create" in content or "adf::kernel::create" in content:
                    header_files.append(fpath)
                elif any(gf != fpath for gf in graph_files):
                    # Could be a graph header included by a graph source
                    header_files.append(fpath)

    # For graph files (headers or sources), find kernel source paths from adf::source()
    # and match them to #include directives for the corresponding .h files
    source_pattern = re.compile(r'(?:adf::)?source\s*\(\s*\w+\s*\)\s*=\s*"([^"]+\.cc)"')
    alt_source_pattern = re.compile(r'(?:adf::)?source\s*\(\s*\w+\s*\)\s*=\s*"([^"]+\.cpp)"')
    include_pattern = re.compile(r'(#include\s+"([^"]+)")')

    # Strategy: look at all files that have kernel::create or adf::source
    # Find the kernel .cc paths, derive the .h name, then find includes of that .h
    all_relevant_files = set(graph_files) | set(header_files)

    for fpath in project_files:
        content = project_files[fpath]
        # Find adf::source paths in this file or related files
        kernel_sources: list[str] = []
        for m in source_pattern.finditer(content):
            kernel_sources.append(m.group(1))
        for m in alt_source_pattern.finditer(content):
            kernel_sources.append(m.group(1))

        if not kernel_sources:
            continue

        # Derive expected kernel header names from source paths
        kernel_headers: list[str] = []
        for ks in kernel_sources:
            # e.g., "kernels/mykernel.cc" -> "mykernel.h" or "kernels/mykernel.h"
            base = re.sub(r'\.(cc|cpp)$', '.h', ks)
            kernel_headers.append(base)
            # Also just the filename
            just_name = base.split('/')[-1]
            if just_name != base:
                kernel_headers.append(just_name)

        # Now look for #include of these kernel headers in graph header files
        # The graph header might be a different file that's included by this file,
        # or it could be this file itself if it's a header
        files_to_check = [fpath]
        # Also check all header files for includes of these kernel headers
        for hf in project_files:
            if hf.endswith((".h", ".hpp")) and hf not in files_to_check:
                files_to_check.append(hf)

        for check_fpath in files_to_check:
            check_content = project_files[check_fpath]
            for inc_match in include_pattern.finditer(check_content):
                full_include = inc_match.group(1)
                included_path = inc_match.group(2)

                # Check if this include matches any kernel header
                for kh in kernel_headers:
                    if included_path == kh or included_path.endswith('/' + kh.split('/')[-1]):
                        # This is a candidate: removing/misnameing this include will cause
                        # undeclared identifier for the kernel function
                        start_pos = inc_match.start()
                        end_pos = inc_match.end()

                        # Find line boundaries to remove the whole line
                        line_start = check_content.rfind('\n', 0, start_pos) + 1
                        line_end = check_content.find('\n', end_pos)
                        if line_end == -1:
                            line_end = len(check_content)
                        else:
                            line_end += 1  # include the newline

                        original_line = check_content[line_start:line_end]

                        # Create a misnamed version (comment it out or rename)
                        # Strategy: misname the header by adding "_MISSING" before extension
                        misnamed_path = re.sub(r'\.h(pp)?$', r'_MISSING.h\1', included_path)
                        replacement_line = original_line.replace(included_path, misnamed_path)

                        candidate = {
                            "file_path": check_fpath,
                            "bug_type": "missing_adf_source_path_header_dependency",
                            "category": "include_headers",
                            "start": line_start,
                            "end": line_end,
                            "original": original_line,
                            "replacement": replacement_line,
                            "description": (
                                f"Misname the #include of kernel header '{included_path}' to "
                                f"'{misnamed_path}' in '{check_fpath}', causing undeclared "
                                f"identifier errors for kernel functions even though "
                                f"adf::source() correctly points to the kernel .cc file."
                            ),
                        }
                        # Avoid duplicates
                        if not any(
                            c["file_path"] == candidate["file_path"]
                            and c["start"] == candidate["start"]
                            for c in candidates
                        ):
                            candidates.append(candidate)

    # If no candidates found via adf::source matching, try a broader approach:
    # Look for graph headers with kernel::create and #include of .h files that
    # look like kernel headers (heuristic: non-adf, non-system headers)
    if not candidates:
        for fpath, content in project_files.items():
            if not (fpath.endswith((".h", ".hpp", ".cc", ".cpp"))):
                continue
            if "kernel::create" not in content and "adf::kernel::create" not in content:
                continue

            # Find kernel function names from kernel::create calls
            create_pattern = re.compile(
                r'(?:adf::)?kernel::create\s*\(\s*(\w+)'
            )
            kernel_funcs = [m.group(1) for m in create_pattern.finditer(content)]

            if not kernel_funcs:
                continue

            # Find includes that might declare these kernel functions
            for inc_match in include_pattern.finditer(content):
                full_include = inc_match.group(1)
                included_path = inc_match.group(2)

                # Skip adf.h and system-like headers
                if 'adf' in included_path.lower() and 'kernel' not in included_path.lower():
                    continue
                if included_path.startswith("adf"):
                    continue

                # Check if the included file exists and declares any kernel function
                # Or heuristically match by name
                inc_basename = included_path.split('/')[-1].replace('.h', '').replace('.hpp', '')

                # Check if any kernel function name relates to this header
                is_kernel_header = False
                for kf in kernel_funcs:
                    if inc_basename.lower() in kf.lower() or kf.lower() in inc_basename.lower():
                        is_kernel_header = True
                        break

                # Also check if the included file content declares the function
                if not is_kernel_header:
                    for fp2, content2 in project_files.items():
                        if fp2.endswith(included_path) or fp2 == included_path:
                            for kf in kernel_funcs:
                                if kf in content2:
                                    is_kernel_header = True
                                    break
                            break

                if not is_kernel_header:
                    continue

                start_pos = inc_match.start()
                end_pos = inc_match.end()
                line_start = content.rfind('\n', 0, start_pos) + 1
                line_end = content.find('\n', end_pos)
                if line_end == -1:
                    line_end = len(content)
                else:
                    line_end += 1

                original_line = content[line_start:line_end]
                misnamed_path = re.sub(r'\.h(pp)?$', r'_MISSING.h\1', included_path)
                replacement_line = original_line.replace(included_path, misnamed_path)

                candidate = {
                    "file_path": fpath,
                    "bug_type": "missing_adf_source_path_header_dependency",
                    "category": "include_headers",
                    "start": line_start,
                    "end": line_end,
                    "original": original_line,
                    "replacement": replacement_line,
                    "description": (
                        f"Misname the #include of kernel header '{included_path}' to "
                        f"'{misnamed_path}' in '{fpath}', causing undeclared identifier "
                        f"errors for kernel function(s) {kernel_funcs} used in kernel::create."
                    ),
                }
                if not any(
                    c["file_path"] == candidate["file_path"]
                    and c["start"] == candidate["start"]
                    for c in candidates
                ):
                    candidates.append(candidate)

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply the mutation to produce a new copy of project_files."""
    new_files = dict(project_files)  # shallow copy of the dict
    fpath = candidate["file_path"]
    content = new_files[fpath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[fpath] = new_content
    return new_files
