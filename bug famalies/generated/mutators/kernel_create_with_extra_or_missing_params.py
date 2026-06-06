import re
import copy

BUG_FAMILY = {
    "family_id": "BF029",
    "bug_type": "kernel_create_with_extra_or_missing_params",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["kernel::create(", "adf::kernel::create("],
    "mutation_strategy": "Add extra arguments to kernel::create() that don't correspond to any constructor or template parameters, or remove required arguments for kernels that use runtime parameters. For example, pass literal values as arguments to kernel::create when the kernel function takes no constructor args, or omit the function pointer entirely.",
    "repair_expectation": "Provide exactly the correct arguments to kernel::create matching the kernel function's expected creation signature.",
    "validation_signal": "WSL Vitis/AIE compile failure with too many/too few arguments to kernel::create or no matching overload.",
    "tags": [
        "argument_count",
        "compile_error",
        "graph_kernel_binding",
        "kernel_create",
        "overload_resolution"
    ]
}

# Pattern to match kernel::create(...) calls, capturing the full expression
# including nested parentheses
_KERNEL_CREATE_PATTERN = re.compile(
    r'((?:adf::)?kernel::create\s*)\(([^;]*?)\)',
    re.DOTALL
)


def _is_graph_header(file_path):
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Common patterns for graph headers
    if lower.endswith('.h') or lower.endswith('.hpp'):
        return True
    # Also check if it contains "graph" in the name
    return False


def _find_matching_paren(text, start):
    """Find the matching closing parenthesis starting from the opening paren at 'start'."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        # Check if this looks like a graph header
        if not (file_path.endswith('.h') or file_path.endswith('.hpp')):
            continue

        # Search for kernel::create( patterns
        # We need to handle nested parens properly
        search_pos = 0
        while search_pos < len(content):
            # Find next occurrence of kernel::create
            match = None
            for pattern_str in ["adf::kernel::create", "kernel::create"]:
                idx = content.find(pattern_str, search_pos)
                if idx != -1:
                    if match is None or idx < match[0]:
                        match = (idx, pattern_str)

            if match is None:
                break

            idx, pattern_str = match
            # Find the opening paren
            paren_start = content.find('(', idx + len(pattern_str))
            if paren_start == -1:
                search_pos = idx + len(pattern_str)
                continue

            # Find matching closing paren
            paren_end = _find_matching_paren(content, paren_start)
            if paren_end == -1:
                search_pos = paren_start + 1
                continue

            # Extract the full call expression
            full_start = idx
            full_end = paren_end + 1
            original = content[full_start:full_end]
            args_text = content[paren_start + 1:paren_end].strip()

            # Determine the prefix (e.g., "kernel::create" or "adf::kernel::create")
            prefix = content[idx:paren_start].rstrip()

            # Generate mutation: add extra arguments
            if args_text:
                # There are existing arguments - add an extra spurious one
                replacement = "{}({}, 0, 1)".format(prefix, args_text)
                description = "Added extra literal arguments (0, 1) to kernel::create() call that don't match any expected parameters"
            else:
                # No arguments - add spurious arguments
                replacement = "{}(0, 1)".format(prefix)
                description = "Added spurious literal arguments (0, 1) to kernel::create() call that takes no constructor arguments"

            candidates.append({
                "file_path": file_path,
                "bug_type": "kernel_create_with_extra_or_missing_params",
                "category": "graph_kernel_binding",
                "start": full_start,
                "end": full_end,
                "original": original,
                "replacement": replacement,
                "description": description
            })

            # Also generate a "remove arguments" mutation if there are args
            if args_text:
                # Remove all arguments (omit function pointer)
                replacement2 = "{}()".format(prefix)
                description2 = "Removed all arguments from kernel::create() call, omitting the required function pointer"
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "kernel_create_with_extra_or_missing_params",
                    "category": "graph_kernel_binding",
                    "start": full_start,
                    "end": full_end,
                    "original": original,
                    "replacement": replacement2,
                    "description": description2
                })

            search_pos = full_end

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected location
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
