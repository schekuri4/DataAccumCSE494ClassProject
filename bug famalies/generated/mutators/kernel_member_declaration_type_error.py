import re
import copy

BUG_FAMILY = {
    "family_id": "BF025",
    "bug_type": "kernel_member_declaration_type_error",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["kernel", "adf::kernel", "port<input>", "port<output>"],
    "mutation_strategy": "Change the member declaration type of a kernel object in the graph class—e.g., declare it as 'port<input>' instead of 'kernel', or as 'parameter' instead of 'kernel', or misspell as 'kernal'. Alternatively, declare it as an array when scalar is expected or vice versa.",
    "repair_expectation": "Restore the correct 'kernel' type declaration for the kernel member variable in the graph class.",
    "validation_signal": "WSL Vitis/AIE compile failure with type error on kernel::create assignment or no member named 'create' in the declared type.",
    "tags": ["graph_class", "graph_kernel_binding", "kernel", "member_declaration", "type_error"]
}

# Replacement options for kernel type declarations
_REPLACEMENTS = [
    ("port<input>", "Replace kernel type with port<input>"),
    ("port<output>", "Replace kernel type with port<output>"),
    ("parameter", "Replace kernel type with parameter"),
    ("kernal", "Misspell kernel as kernal"),
]


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h/.hpp files with 'graph' in name or content."""
    return file_path.endswith(('.h', '.hpp'))


def _file_looks_like_graph_header(content):
    """Check if file content contains graph class patterns."""
    # Look for adf::graph or public graph inheritance or kernel usage
    if re.search(r'(adf::graph|public\s+graph|class\s+\w+\s*:\s*public\s+(adf::)?graph)', content):
        return True
    if re.search(r'kernel\s*::\s*create', content):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue
        if not _file_looks_like_graph_header(content):
            continue

        # Match kernel member declarations like:
        # kernel k;
        # adf::kernel k;
        # kernel k1, k2;
        # kernel k[N];
        # Also inside class body
        pattern = re.compile(
            r'^([ \t]*)((?:adf::)?kernel)\s+(\w+(?:\s*\[\s*\w+\s*\])?(?:\s*,\s*\w+(?:\s*\[\s*\w+\s*\])?)*)\s*;',
            re.MULTILINE
        )

        for match in pattern.finditer(content):
            indent = match.group(1)
            type_decl = match.group(2)  # "kernel" or "adf::kernel"
            var_names = match.group(3)
            full_match = match.group(0)
            start = match.start()
            end = match.end()

            # Check if this is truly a member declaration (not inside a function creating kernels)
            # We look for kernel::create usage of these variable names as confirmation
            # but it's not strictly required

            # Generate mutation candidates
            for replacement_type, desc_suffix in _REPLACEMENTS:
                # Build the replacement line
                replacement_line = f"{indent}{replacement_type} {var_names};"
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "kernel_member_declaration_type_error",
                    "category": "graph_kernel_binding",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement_line,
                    "description": f"{desc_suffix}: '{type_decl} {var_names}' -> '{replacement_type} {var_names}'"
                })

            # Also: if it's a scalar, mutate to array; if array, mutate to scalar
            var_list = [v.strip() for v in var_names.split(',')]
            for var in var_list:
                if re.search(r'\[', var):
                    # It's an array, mutate to scalar
                    scalar_name = re.sub(r'\s*\[.*?\]', '', var)
                    new_var_names = var_names.replace(var, scalar_name)
                    replacement_line = f"{indent}{type_decl} {new_var_names};"
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "kernel_member_declaration_type_error",
                        "category": "graph_kernel_binding",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement_line,
                        "description": f"Change kernel array to scalar: '{var}' -> '{scalar_name}'"
                    })
                else:
                    # It's a scalar, mutate to array
                    array_name = f"{var}[4]"
                    new_var_names = var_names.replace(var, array_name)
                    replacement_line = f"{indent}{type_decl} {new_var_names};"
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "kernel_member_declaration_type_error",
                        "category": "graph_kernel_binding",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement_line,
                        "description": f"Change kernel scalar to array: '{var}' -> '{array_name}'"
                    })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
