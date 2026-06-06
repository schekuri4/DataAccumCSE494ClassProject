import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF021",
    "bug_type": "kernel_create_wrong_function_pointer",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["kernel::create(", "adf::kernel::create("],
    "mutation_strategy": "Replace the function pointer argument in kernel::create() with a different kernel function that has an incompatible signature (e.g., different number of parameters, different types such as passing a function expecting input_window<int32> when the port connections expect input_window<cint16>), or point to a non-existent function name.",
    "repair_expectation": "Restore the correct function pointer that matches the declared kernel prototype and the graph port connections.",
    "validation_signal": "WSL Vitis/AIE compile failure with linker or template instantiation error indicating kernel function signature mismatch or undefined reference.",
    "tags": [
        "function_pointer",
        "graph_binding",
        "graph_kernel_binding",
        "kernel_create",
        "signature_mismatch",
    ],
}

# Pattern to match kernel::create(...) or adf::kernel::create(...)
# Captures the function pointer argument inside the parentheses
_KERNEL_CREATE_PATTERN = re.compile(
    r'((?:adf::)?kernel::create\s*\(\s*)'  # group 1: prefix up to and including '('
    r'([A-Za-z_][A-Za-z0-9_:]*(?:<[^>]*>)?)'  # group 2: function pointer (possibly templated)
    r'(\s*\))'  # group 3: closing paren
)

# Also handle kernel::create with multiple template args or qualified names
_KERNEL_CREATE_PATTERN_EXTENDED = re.compile(
    r'((?:adf::)?kernel::create\s*(?:<[^>]*>\s*)?\(\s*)'  # group 1: prefix including optional template
    r'([A-Za-z_][A-Za-z0-9_:]*(?:\s*<[^>]*>)?)'  # group 2: function pointer
    r'(\s*\))'  # group 3: closing paren
)


def _is_graph_header(file_path: str) -> bool:
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Common patterns for graph headers in AIE projects
    if lower.endswith('.h') or lower.endswith('.hpp'):
        # Prefer files with 'graph' in the name, but also consider any header
        return True
    return False


def _collect_all_kernel_functions(content: str) -> list[str]:
    """Extract all unique function names used in kernel::create calls in the file."""
    functions = set()
    for pattern in [_KERNEL_CREATE_PATTERN, _KERNEL_CREATE_PATTERN_EXTENDED]:
        for m in pattern.finditer(content):
            func_name = m.group(2).strip()
            functions.add(func_name)
    return sorted(functions)


def _generate_wrong_function(original_func: str, all_functions: list[str]) -> str:
    """Generate a replacement function pointer that differs from the original."""
    # First try to use another function from the same file
    for func in all_functions:
        if func != original_func:
            return func

    # If no other function available, generate a non-existent function name
    return original_func + "_wrong_signature"


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Check if file contains kernel::create patterns
        has_kernel_create = ("kernel::create(" in content)
        if not has_kernel_create:
            continue

        # Collect all kernel function names in this file for cross-replacement
        all_functions = _collect_all_kernel_functions(content)

        # Try both patterns
        seen_positions: set[tuple[int, int]] = set()

        for pattern in [_KERNEL_CREATE_PATTERN_EXTENDED, _KERNEL_CREATE_PATTERN]:
            for m in pattern.finditer(content):
                start = m.start(2)
                end = m.end(2)

                if (start, end) in seen_positions:
                    continue
                seen_positions.add((start, end))

                original_func = m.group(2).strip()
                replacement_func = _generate_wrong_function(original_func, all_functions)

                if replacement_func == original_func:
                    continue

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "kernel_create_wrong_function_pointer",
                    "category": "graph_kernel_binding",
                    "start": start,
                    "end": end,
                    "original": original_func,
                    "replacement": replacement_func,
                    "description": (
                        f"Replace kernel function pointer '{original_func}' with "
                        f"'{replacement_func}' in kernel::create() call, causing a "
                        f"signature mismatch or undefined reference."
                    ),
                })

    return candidates


def apply_mutation(
    project_files: dict[str, str], candidate: dict[str, object]
) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]

    new_files[file_path] = new_content
    return new_files
