import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF022",
    "bug_type": "kernel_create_missing_template_argument",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["kernel::create<", "adf::kernel::create<"],
    "mutation_strategy": "For templated kernel functions (e.g., kernel::create<fir_filter<16, cint16>>(...)), remove or alter the template arguments—either omit them entirely, provide wrong types (int instead of cint16), or provide wrong integer template parameters (e.g., wrong filter length).",
    "repair_expectation": "Provide the correct template arguments matching the kernel function template definition.",
    "validation_signal": "WSL Vitis/AIE compile failure with template argument deduction failure or no matching function for call to kernel::create.",
    "tags": [
        "compile_error",
        "graph_kernel_binding",
        "kernel_create",
        "template_arguments",
        "templated_kernel"
    ]
}


def _is_graph_header(file_path: str) -> bool:
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Common patterns for graph headers
    if lower.endswith('.h') or lower.endswith('.hpp'):
        return True
    # Also consider .cpp files that might contain graph definitions
    if lower.endswith('.cpp') or lower.endswith('.cc'):
        if 'graph' in lower:
            return True
    return False


def _find_matching_angle_bracket(text: str, start: int) -> int:
    """Find the matching closing '>' for an opening '<' at position start.
    Handles nested angle brackets."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
            if depth == 0:
                return i
        elif ch == '(' or ch == ')':
            # If we hit parentheses before closing, something is wrong
            if depth <= 0:
                return -1
        i += 1
    return -1


def _generate_mutations_for_template(template_content: str) -> list[tuple[str, str]]:
    """Generate various mutations for a template argument string.
    Returns list of (replacement, description) tuples."""
    mutations = []

    # Mutation 1: Remove template arguments entirely (just empty)
    mutations.append(("", "Remove all template arguments from kernel::create"))

    # Mutation 2: If there are integer parameters, alter them
    # Find integer literals and change them
    int_pattern = re.compile(r'\b(\d+)\b')
    int_matches = list(int_pattern.finditer(template_content))
    if int_matches:
        # Change first integer to a different value
        m = int_matches[0]
        orig_val = int(m.group(1))
        new_val = orig_val * 2 if orig_val != 0 else 1
        altered = template_content[:m.start()] + str(new_val) + template_content[m.end():]
        mutations.append((altered, f"Change template integer parameter from {orig_val} to {new_val}"))

    # Mutation 3: Replace type arguments with wrong types
    # Look for common AIE types and replace them
    type_replacements = {
        'cint16': 'int',
        'cint32': 'int',
        'cfloat': 'float',
        'int16': 'int',
        'int32': 'int',
        'int8': 'int',
        'float': 'int',
    }
    for orig_type, new_type in type_replacements.items():
        if orig_type in template_content:
            altered = template_content.replace(orig_type, new_type, 1)
            mutations.append((altered, f"Replace template type '{orig_type}' with '{new_type}'"))
            break

    return mutations


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find all kernel::create<...> calls with template arguments in graph headers."""
    candidates: list[dict[str, object]] = []

    # Pattern to find kernel::create< with optional adf:: prefix
    # We need to find the full expression including template args
    pattern = re.compile(r'(?:adf::)?kernel::create<')
    create_function_template_pattern = re.compile(
        r'((?:adf::)?kernel::create\s*\(\s*[A-Za-z_][A-Za-z0-9_:]*)(<)'
    )
    create_object_pattern = re.compile(r'(?:adf::)?kernel::create_object<')

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        for match in pattern.finditer(content):
            # Find the opening '<' of the template arguments
            # The match ends right after 'create<'
            angle_start = match.end() - 1  # position of '<'

            # Find matching '>'
            angle_end = _find_matching_angle_bracket(content, angle_start)
            if angle_end < 0:
                continue

            # Extract the template arguments (between < and >)
            template_args = content[angle_start + 1:angle_end]

            if not template_args.strip():
                # Already empty template args, skip
                continue

            # The full original text from start of match to closing >
            full_original = content[match.start():angle_end + 1]

            # Generate mutations
            mutations = _generate_mutations_for_template(template_args)

            for replacement_args, description in mutations:
                # Build the replacement string
                prefix = content[match.start():angle_start]  # e.g., "kernel::create" or "adf::kernel::create"

                if replacement_args == "":
                    # Remove template arguments entirely: kernel::create(...)
                    replacement = prefix
                else:
                    replacement = prefix + "<" + replacement_args + ">"

                candidate = {
                    "file_path": file_path,
                    "bug_type": "kernel_create_missing_template_argument",
                    "category": "graph_kernel_binding",
                    "start": match.start(),
                    "end": angle_end + 1,
                    "original": full_original,
                    "replacement": replacement,
                    "description": description
                }
                candidates.append(candidate)

        for match in create_function_template_pattern.finditer(content):
            angle_start = match.start(2)
            angle_end = _find_matching_angle_bracket(content, angle_start)
            if angle_end < 0:
                continue
            template_args = content[angle_start + 1:angle_end]
            if not template_args.strip():
                continue
            original = content[match.start():angle_end + 1]
            replacement = content[match.start():angle_start]
            candidates.append({
                "file_path": file_path,
                "bug_type": "kernel_create_missing_template_argument",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": angle_end + 1,
                "original": original,
                "replacement": replacement,
                "description": "Remove template arguments from function passed to kernel::create(...).",
            })

        for match in create_object_pattern.finditer(content):
            angle_start = match.end() - 1
            angle_end = _find_matching_angle_bracket(content, angle_start)
            if angle_end < 0:
                continue
            template_args = content[angle_start + 1:angle_end]
            if not template_args.strip():
                continue
            original = content[match.start():angle_end + 1]
            replacement = content[match.start():angle_start]
            candidates.append({
                "file_path": file_path,
                "bug_type": "kernel_create_missing_template_argument",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": angle_end + 1,
                "original": original,
                "replacement": replacement,
                "description": "Remove template arguments from kernel::create_object<...>.",
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
