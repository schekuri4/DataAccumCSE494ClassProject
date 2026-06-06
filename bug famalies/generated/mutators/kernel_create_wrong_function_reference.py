import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF060",
    "bug_type": "kernel_create_wrong_function_reference",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "kernel::create(",
        "adf::kernel::create",
        "source("
    ],
    "mutation_strategy": "In kernel::create(), reference a function name that does not match any declared kernel function, or reference a kernel function with wrong template arguments. For example, use kernel::create(filter_wrong) instead of kernel::create(fir_filter) where fir_filter is the actual kernel function. This may be combined with a correct adf::source() path to isolate the error to the create call.",
    "repair_expectation": "Correct the function reference in kernel::create() to match the actual kernel function signature declared in the kernel header.",
    "validation_signal": "WSL Vitis/AIE compile failure with undefined reference or no matching function for kernel::create.",
    "tags": [
        "function_reference",
        "graph_connections",
        "graph_constructor",
        "kernel_create",
        "undeclared"
    ]
}


def _is_graph_header(file_path: str) -> bool:
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Common patterns for graph headers in AIE projects
    if lower.endswith('.h') or lower.endswith('.hpp'):
        return True
    return False


def _mangle_function_name(name: str) -> str:
    """Create a wrong function name by appending '_wrong' or modifying it."""
    # Strip whitespace
    name = name.strip()
    if name.endswith('_wrong'):
        return name + '_x'
    return name + '_wrong'


def _mangle_template_args(full_match: str, func_name: str, template_args: str) -> str:
    """Modify template arguments to create a mismatch."""
    # Try to change numeric template args
    numbers = re.findall(r'\d+', template_args)
    if numbers:
        # Change the first number
        first_num = numbers[0]
        new_num = str(int(first_num) + 1)
        new_template = template_args.replace(first_num, new_num, 1)
        return func_name + new_template
    # If no numbers, mangle the function name instead
    return _mangle_function_name(func_name) + template_args


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match kernel::create( ... ) with optional adf:: prefix
    # Captures the function reference inside create(...)
    # Handles: kernel::create(func_name) or kernel::create(func_name<T, N>)
    create_pattern = re.compile(
        r'((?:adf::)?kernel::create\s*\(\s*)'  # group 1: prefix up to function ref
        r'([a-zA-Z_]\w*(?:\s*<[^>]*>)?)'       # group 2: function reference (with optional template)
        r'(\s*\))',                               # group 3: closing
        re.DOTALL
    )

    # Also handle create_kernel or similar patterns
    create_pattern_alt = re.compile(
        r'((?:adf::)?kernel::create\s*<\s*)'    # group 1: kernel::create<
        r'([a-zA-Z_]\w*(?:\s*<[^>]*>)?)'       # group 2: function reference
        r'(\s*>)',                               # group 3: closing >
        re.DOTALL
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Check if file contains kernel::create patterns
        if 'kernel::create' not in content and 'kernel::create' not in content.lower():
            continue

        # Search for kernel::create(function_name) pattern
        for match in create_pattern.finditer(content):
            prefix = match.group(1)
            func_ref = match.group(2)
            suffix = match.group(3)

            original_full = match.group(0)
            start = match.start()
            end = match.end()

            # Determine replacement function reference
            # Check if there are template arguments
            template_match = re.match(r'([a-zA-Z_]\w*)\s*(<[^>]*>)', func_ref)
            if template_match:
                func_name = template_match.group(1)
                template_args = template_match.group(2)
                new_func_ref = _mangle_template_args(func_ref, func_name, template_args)
            else:
                new_func_ref = _mangle_function_name(func_ref)

            replacement_full = prefix + new_func_ref + suffix

            candidates.append({
                "file_path": file_path,
                "bug_type": "kernel_create_wrong_function_reference",
                "category": "graph_connections",
                "start": start,
                "end": end,
                "original": original_full,
                "replacement": replacement_full,
                "description": (
                    f"Changed kernel::create() function reference from '{func_ref}' "
                    f"to '{new_func_ref}' to introduce an undefined/wrong function reference."
                )
            })

        # Also try the template-style: kernel::create<func_name>(...)
        for match in create_pattern_alt.finditer(content):
            prefix = match.group(1)
            func_ref = match.group(2)
            suffix = match.group(3)

            original_full = match.group(0)
            start = match.start()
            end = match.end()

            template_match = re.match(r'([a-zA-Z_]\w*)\s*(<[^>]*>)', func_ref)
            if template_match:
                func_name = template_match.group(1)
                template_args = template_match.group(2)
                new_func_ref = _mangle_template_args(func_ref, func_name, template_args)
            else:
                new_func_ref = _mangle_function_name(func_ref)

            replacement_full = prefix + new_func_ref + suffix

            candidates.append({
                "file_path": file_path,
                "bug_type": "kernel_create_wrong_function_reference",
                "category": "graph_connections",
                "start": start,
                "end": end,
                "original": original_full,
                "replacement": replacement_full,
                "description": (
                    f"Changed kernel::create<> function reference from '{func_ref}' "
                    f"to '{new_func_ref}' to introduce an undefined/wrong function reference."
                )
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    content = new_files[file_path]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
