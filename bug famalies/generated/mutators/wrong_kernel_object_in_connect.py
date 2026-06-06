BUG_FAMILY = {
    "family_id": "BF056",
    "bug_type": "wrong_kernel_object_in_connect",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["kernel", "connect<", ".out[", ".in[", "kernel::create"],
    "mutation_strategy": "Reference a wrong or nonexistent kernel object name in a connect statement, e.g., if the graph has kernel k1 and k2, use k3 or misspell as k1_ in the connect call. This produces an undeclared identifier error.",
    "repair_expectation": "Replace the incorrect kernel object reference with the correct declared kernel member name.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared identifier or 'was not declared in this scope' error.",
    "tags": ["connect", "graph_connections", "kernel_reference", "object_name", "undeclared"]
}

import re
from copy import deepcopy


def _is_graph_header(filepath):
    """Heuristic: a graph header is a .h or .hpp file containing 'graph' class and kernel declarations."""
    lower = filepath.lower()
    return lower.endswith('.h') or lower.endswith('.hpp')


def _find_kernel_names(content):
    """Find kernel member variable names declared via 'kernel <name>' patterns."""
    # Matches: kernel k1; or kernel k1, k2; or adf::kernel k1;
    names = set()
    # Pattern for kernel declarations like: kernel k1; or kernel k1, k2;
    decl_pattern = re.compile(r'(?:adf::)?kernel\s+([\w\s,]+)\s*;')
    for m in decl_pattern.finditer(content):
        ids = m.group(1).split(',')
        for id_str in ids:
            id_str = id_str.strip()
            if id_str and re.match(r'^[a-zA-Z_]\w*$', id_str):
                names.add(id_str)
    return names


def _generate_wrong_name(kernel_name, all_kernel_names):
    """Generate a plausible wrong kernel name that doesn't exist."""
    # Try appending underscore
    candidate = kernel_name + "_"
    if candidate not in all_kernel_names:
        return candidate
    # Try prepending underscore
    candidate = "_" + kernel_name
    if candidate not in all_kernel_names:
        return candidate
    # Try incrementing trailing number
    match = re.match(r'^(.*?)(\d+)$', kernel_name)
    if match:
        base = match.group(1)
        num = int(match.group(2))
        candidate = base + str(num + 1)
        if candidate not in all_kernel_names:
            return candidate
    # Fallback: append "x"
    candidate = kernel_name + "x"
    if candidate not in all_kernel_names:
        return candidate
    return kernel_name + "__nonexistent"


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_header(filepath):
            continue

        # Check if file has kernel declarations and connect statements
        kernel_names = _find_kernel_names(content)
        if not kernel_names:
            continue

        # Find connect statements that reference kernel objects with .in[ or .out[
        # Pattern: connect< ... >( kernelname.out[N], kernelname.in[N] )
        # We look for kernel names used in connect calls with .in[ or .out[
        connect_pattern = re.compile(
            r'(connect\s*<[^>]*>\s*\(\s*)'
            r'([a-zA-Z_]\w*)'
            r'(\s*\.\s*(?:out|in)\s*\[)'
        )

        for m in connect_pattern.finditer(content):
            used_name = m.group(2)
            if used_name not in kernel_names:
                continue  # Only mutate references to actual declared kernels

            wrong_name = _generate_wrong_name(used_name, kernel_names)

            # The full match region for the kernel name part
            start = m.start(2)
            end = m.end(2)

            candidates.append({
                "file_path": filepath,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": used_name,
                "replacement": wrong_name,
                "description": (
                    f"Replace kernel object '{used_name}' with nonexistent '{wrong_name}' "
                    f"in connect statement to cause undeclared identifier error."
                )
            })

        # Also look for second argument pattern: , kernelname.in[
        connect_arg2_pattern = re.compile(
            r'(,\s*)'
            r'([a-zA-Z_]\w*)'
            r'(\s*\.\s*(?:out|in)\s*\[)'
        )

        for m in connect_arg2_pattern.finditer(content):
            used_name = m.group(2)
            if used_name not in kernel_names:
                continue

            # Verify this is inside a connect statement context
            line_start = content.rfind('\n', 0, m.start()) + 1
            line = content[line_start:content.find('\n', m.end())]
            if 'connect' not in line and 'connect' not in content[max(0, m.start()-200):m.start()]:
                continue

            wrong_name = _generate_wrong_name(used_name, kernel_names)

            start = m.start(2)
            end = m.end(2)

            # Avoid duplicates
            already_exists = any(
                c["file_path"] == filepath and c["start"] == start and c["end"] == end
                for c in candidates
            )
            if already_exists:
                continue

            candidates.append({
                "file_path": filepath,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": used_name,
                "replacement": wrong_name,
                "description": (
                    f"Replace kernel object '{used_name}' with nonexistent '{wrong_name}' "
                    f"in connect statement argument to cause undeclared identifier error."
                )
            })

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
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]

    new_files[filepath] = new_content
    return new_files
