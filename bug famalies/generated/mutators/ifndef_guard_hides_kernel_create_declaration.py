import re
import copy

BUG_FAMILY = {
    "family_id": "BF016",
    "bug_type": "ifndef_guard_hides_kernel_create_declaration",
    "category": "header_guards_and_preprocessor",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::kernel::create",
        "adf::source",
        "#ifndef",
        "kernel function declaration"
    ],
    "mutation_strategy": "Place the kernel::create() call and adf::source() path assignment inside an #ifndef block guarded by a macro that is always defined (e.g., #ifndef __cplusplus), causing the kernel creation and source path to be excluded from compilation, resulting in uninitialized kernel objects in the graph.",
    "repair_expectation": "Remove the erroneous #ifndef guard or change it to a macro that is not defined during normal AIE compilation.",
    "validation_signal": "WSL Vitis/AIE compile failure with errors about uninitialized kernel objects or missing source file assignments.",
    "tags": [
        "adf_source",
        "always_defined",
        "graph",
        "header_guards_and_preprocessor",
        "ifndef",
        "kernel_create"
    ]
}


def _is_graph_header(file_path, content):
    """Heuristic to identify graph header files."""
    # Must be a header file
    if not (file_path.endswith('.h') or file_path.endswith('.hpp')):
        return False
    # Should contain graph-related content
    if 'graph' in content.lower() or 'adf' in content.lower():
        # Should contain kernel::create or source references
        if 'kernel::create' in content or 'create(' in content:
            return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path, content):
            continue

        # Find blocks containing kernel::create and/or adf::source calls
        # We look for consecutive lines that have kernel::create and source assignments
        lines = content.split('\n')

        # Find ranges of lines containing kernel::create and adf::source
        # Strategy: find contiguous groups of statements involving kernel::create and source
        kernel_create_pattern = re.compile(r'.*kernel::create\s*[<(].*')
        source_pattern = re.compile(r'.*(?:adf::)?source\s*\(.*')

        # Collect indices of relevant lines
        relevant_indices = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if kernel_create_pattern.match(stripped) or source_pattern.match(stripped):
                relevant_indices.append(i)

        if not relevant_indices:
            continue

        # Group consecutive or nearby lines (within 3 lines of each other)
        groups = []
        current_group = [relevant_indices[0]]
        for idx in relevant_indices[1:]:
            if idx - current_group[-1] <= 3:
                current_group.append(idx)
            else:
                groups.append(current_group)
                current_group = [idx]
        groups.append(current_group)

        for group in groups:
            # Expand group to include all lines between first and last relevant line
            start_line = group[0]
            end_line = group[-1]

            # Include the full statements - extend to capture complete lines
            # Also include any lines between them that might be part of the block
            original_lines = lines[start_line:end_line + 1]
            original_text = '\n'.join(original_lines)

            # Check that we have at least a kernel::create
            if not any(kernel_create_pattern.match(l.strip()) for l in original_lines):
                continue

            # Build the mutated replacement: wrap in #ifndef __cplusplus
            indent = ''
            # Detect common indentation
            for line in original_lines:
                if line.strip():
                    indent = line[:len(line) - len(line.lstrip())]
                    break

            replacement_lines = [
                indent + '#ifndef __cplusplus  // guard for platform compatibility',
            ] + original_lines + [
                indent + '#endif'
            ]
            replacement_text = '\n'.join(replacement_lines)

            candidate = {
                "file_path": file_path,
                "bug_type": "ifndef_guard_hides_kernel_create_declaration",
                "category": "header_guards_and_preprocessor",
                "start": start_line,
                "end": end_line,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    "Wrapped kernel::create() and adf::source() assignments inside "
                    "#ifndef __cplusplus block. Since __cplusplus is always defined in C++ "
                    "compilation, these statements will be excluded, leaving kernel objects "
                    "uninitialized."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start_line = candidate["start"]
    end_line = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    lines = content.split('\n')

    # Verify the original text matches
    actual_text = '\n'.join(lines[start_line:end_line + 1])
    if actual_text == original:
        new_lines = lines[:start_line] + replacement.split('\n') + lines[end_line + 1:]
        new_files[file_path] = '\n'.join(new_lines)
    else:
        # Fallback: use string replacement
        new_files[file_path] = content.replace(original, replacement, 1)

    return new_files
