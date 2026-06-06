import re
import copy

BUG_FAMILY = {
    "family_id": "BF035",
    "bug_type": "kernel_create_wrong_function_target",
    "category": "kernel_prototypes_and_signatures",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "kernel::create(",
        "adf::kernel::create("
    ],
    "mutation_strategy": "Change the function pointer passed to kernel::create() to reference a different function name that either does not exist or has an incompatible signature. For example, change kernel::create(fir_filter) to kernel::create(fft_filter) where fft_filter is not declared or has different parameters.",
    "repair_expectation": "Correct the kernel::create() call to reference the intended kernel function with the matching prototype.",
    "validation_signal": "WSL Vitis/AIE compile failure with undefined reference or signature mismatch for the kernel function target.",
    "tags": [
        "kernel_create",
        "kernel_prototypes_and_signatures",
        "undefined_function",
        "wrong_target"
    ]
}

# Pattern to match kernel::create(...) or adf::kernel::create(...) calls
# Captures the full match and the function name argument
_KERNEL_CREATE_PATTERN = re.compile(
    r'((?:adf::)?kernel::create\s*\(\s*)'  # group 1: prefix up to and including '('
    r'([A-Za-z_][A-Za-z0-9_]*)'             # group 2: function name
    r'(\s*\))'                               # group 3: closing paren
)


def _generate_wrong_name(original_name):
    """Generate a plausible but wrong function name based on the original."""
    # Strategy: append '_wrong' suffix, or if it already has one, change it
    suffixes = ["_wrong", "_invalid", "_undefined"]
    for suffix in suffixes:
        if not original_name.endswith(suffix):
            return original_name + suffix
    # Fallback: prepend 'wrong_'
    return "wrong_" + original_name


def _is_graph_file(file_path):
    """Heuristic to determine if a file is a graph header or graph source."""
    lower = file_path.lower()
    # Check for common graph file naming patterns
    if 'graph' in lower:
        return True
    # Also consider .h and .cpp files that might be graph files
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        for match in _KERNEL_CREATE_PATTERN.finditer(content):
            prefix = match.group(1)
            func_name = match.group(2)
            suffix = match.group(3)

            original_text = match.group(0)
            wrong_name = _generate_wrong_name(func_name)
            replacement_text = prefix + wrong_name + suffix

            start = match.start()
            end = match.end()

            candidate = {
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed kernel::create() function target from '{func_name}' "
                    f"to '{wrong_name}' which does not exist or has an incompatible "
                    f"signature, causing an undefined reference or signature mismatch."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
