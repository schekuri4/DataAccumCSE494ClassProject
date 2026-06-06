import re
import copy

BUG_FAMILY = {
    "family_id": "BF012",
    "bug_type": "duplicate_include_guard_macro_collision",
    "category": "header_guards_and_preprocessor",
    "target_files": [
        "kernel header",
        "shared utility header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#ifndef",
        "#define",
        "#endif",
        "kernel header guard macro"
    ],
    "mutation_strategy": "Give two different kernel headers the same include guard macro name (e.g., both use #ifndef _KERNEL_H_), causing the second header's declarations (kernel function prototypes, vector intrinsic helpers) to be silently skipped, leading to undeclared function errors when the graph references both kernels.",
    "repair_expectation": "Rename one of the include guard macros to a unique identifier matching its filename.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared identifier or implicit function declaration errors for kernel functions.",
    "tags": [
        "duplicate",
        "header_guards_and_preprocessor",
        "include_guard",
        "kernel_header",
        "macro_collision"
    ]
}


def _extract_include_guard(content):
    """Extract include guard macro from a header file, returning (macro, ifndef_match, define_match) or None."""
    # Match #ifndef MACRO at the start (possibly after comments/blank lines)
    ifndef_pattern = re.compile(r'^[ \t]*#\s*ifndef\s+([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE)
    define_pattern = re.compile(r'^[ \t]*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE)
    
    ifndef_match = ifndef_pattern.search(content)
    if not ifndef_match:
        return None
    
    macro = ifndef_match.group(1)
    
    # Look for matching #define right after
    after_ifndef = content[ifndef_match.end():]
    define_match = define_pattern.search(after_ifndef)
    if not define_match:
        return None
    
    define_macro = define_match.group(1)
    if define_macro != macro:
        return None
    
    return macro


def _is_header_file(path):
    """Check if a file path looks like a header file."""
    return path.endswith('.h') or path.endswith('.hpp') or path.endswith('.hh')


def _is_kernel_or_utility_header(path, content):
    """Heuristic: check if this looks like a kernel header or shared utility header for AIE."""
    path_lower = path.lower()
    # Check path hints
    kernel_hints = ['kernel', 'aie', 'filter', 'fir', 'fft', 'dds', 'util', 'helper', 'common']
    has_path_hint = any(hint in path_lower for hint in kernel_hints)
    
    # Check content hints
    content_hints = ['void', 'inline', 'aie', 'v8int32', 'v16int16', 'v32int8',
                     'adf', 'input_window', 'output_window', 'kernel', '__attribute__']
    has_content_hint = any(hint in content for hint in content_hints)
    
    # If it's a .h file with an include guard, consider it a candidate
    return _is_header_file(path) and (_extract_include_guard(content) is not None) and (has_path_hint or has_content_hint or True)


def find_mutation_candidates(project_files):
    """Find pairs of header files where we can make their include guards collide."""
    candidates = []
    
    # Collect all header files with include guards
    headers_with_guards = []
    for path, content in project_files.items():
        if not _is_header_file(path):
            continue
        guard = _extract_include_guard(content)
        if guard is not None:
            headers_with_guards.append((path, guard, content))
    
    if len(headers_with_guards) < 2:
        return []
    
    # For each pair, create a candidate that changes the second header's guard to match the first
    for i in range(len(headers_with_guards)):
        for j in range(len(headers_with_guards)):
            if i == j:
                continue
            
            path_i, guard_i, content_i = headers_with_guards[i]
            path_j, guard_j, content_j = headers_with_guards[j]
            
            # Skip if they already have the same guard
            if guard_i == guard_j:
                continue
            
            # We'll mutate file j to use file i's guard macro
            # Find the ifndef and define lines in file j
            ifndef_pattern = re.compile(
                r'^([ \t]*#\s*ifndef\s+)' + re.escape(guard_j) + r'\b',
                re.MULTILINE
            )
            define_pattern = re.compile(
                r'^([ \t]*#\s*define\s+)' + re.escape(guard_j) + r'\b',
                re.MULTILINE
            )
            
            ifndef_m = ifndef_pattern.search(content_j)
            define_m = define_pattern.search(content_j)
            
            if not ifndef_m or not define_m:
                continue
            
            # Also check for #endif with comment referencing the guard
            endif_pattern = re.compile(
                r'^([ \t]*#\s*endif\s*/[*/]\s*)' + re.escape(guard_j) + r'\b',
                re.MULTILINE
            )
            
            # Build the replacement content
            new_content = ifndef_pattern.sub(r'\g<1>' + guard_i, content_j)
            new_content = define_pattern.sub(r'\g<1>' + guard_i, new_content)
            # Optionally fix endif comment too
            new_content = endif_pattern.sub(r'\g<1>' + guard_i, new_content)
            
            # Determine the region we're changing (use full file for simplicity)
            candidate = {
                "file_path": path_j,
                "bug_type": "duplicate_include_guard_macro_collision",
                "category": "header_guards_and_preprocessor",
                "start": ifndef_m.start(),
                "end": define_m.end(),
                "original": guard_j,
                "replacement": guard_i,
                "description": (
                    f"Changed include guard in '{path_j}' from '{guard_j}' to '{guard_i}' "
                    f"(same as '{path_i}'), causing macro collision. "
                    f"When both headers are included, the second one's content will be skipped."
                )
            }
            candidates.append(candidate)
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply the include guard collision mutation to the specified file."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    original_guard = candidate["original"]
    replacement_guard = candidate["replacement"]
    
    if file_path not in new_files:
        return new_files
    
    content = new_files[file_path]
    
    # Replace the guard macro in #ifndef and #define directives
    ifndef_pattern = re.compile(
        r'^([ \t]*#\s*ifndef\s+)' + re.escape(original_guard) + r'\b',
        re.MULTILINE
    )
    define_pattern = re.compile(
        r'^([ \t]*#\s*define\s+)' + re.escape(original_guard) + r'\b',
        re.MULTILINE
    )
    endif_pattern = re.compile(
        r'^([ \t]*#\s*endif\s*/[*/]\s*)' + re.escape(original_guard) + r'\b',
        re.MULTILINE
    )
    
    new_content = ifndef_pattern.sub(r'\g<1>' + replacement_guard, content)
    new_content = define_pattern.sub(r'\g<1>' + replacement_guard, new_content)
    new_content = endif_pattern.sub(r'\g<1>' + replacement_guard, new_content)
    
    new_files[file_path] = new_content
    return new_files
