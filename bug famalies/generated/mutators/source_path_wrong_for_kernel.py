import re
import copy
import os

BUG_FAMILY = {
    "family_id": "BF026",
    "bug_type": "source_path_wrong_for_kernel",
    "category": "graph_kernel_binding",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "either",
    "match_targets": ["adf::source(", "source("],
    "mutation_strategy": "Modify the adf::source() call to reference a wrong file path—either a non-existent .cc file, a path with wrong directory prefix, a header file instead of source file, or swap source paths between two different kernels so each kernel points to the other's implementation.",
    "repair_expectation": "Correct the source() path to point to the actual kernel implementation .cc file with proper relative path.",
    "validation_signal": "WSL Vitis/AIE compile failure with file not found error or kernel function undefined due to wrong source mapping.",
    "tags": ["adf_source", "file_reference", "graph_kernel_binding", "kernel_binding", "source_path"]
}

# Pattern to match adf::source(kernel_name) = "path"; or source(kernel_name) = "path";
_SOURCE_PATTERN = re.compile(
    r'((?:adf::)?source\s*\(\s*\w+\s*\)\s*=\s*")([^"]+)(")'
)


def _is_graph_file(filepath):
    """Heuristic: graph headers/sources typically contain graph class or adf::source calls."""
    lower = filepath.lower()
    # Accept .h, .hpp, .cpp, .cc files that might be graph files
    extensions = ('.h', '.hpp', '.cpp', '.cc', '.hh')
    return any(lower.endswith(ext) for ext in extensions)


def _mutate_path(original_path, all_paths, index):
    """Generate a mutated path using various strategies."""
    # Strategy 1: If there are multiple source paths, swap with another
    if len(all_paths) > 1:
        # Swap with the next one (circular)
        other_index = (index + 1) % len(all_paths)
        if all_paths[other_index] != original_path:
            return all_paths[other_index]

    # Strategy 2: Change .cc/.cpp to .h (header instead of source)
    base, ext = os.path.splitext(original_path)
    if ext in ('.cc', '.cpp', '.cxx'):
        return base + '.h'

    # Strategy 3: Add wrong directory prefix
    if '/' in original_path:
        parts = original_path.split('/')
        parts.insert(0, 'wrong_dir')
        return '/'.join(parts)

    # Strategy 4: Reference a non-existent file
    return base + '_nonexistent' + ext


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Check if file contains any source() calls
        matches = list(_SOURCE_PATTERN.finditer(content))
        if not matches:
            continue

        # Collect all source paths in this file for potential swapping
        all_paths = [m.group(2) for m in matches]

        for idx, match in enumerate(matches):
            original_path = match.group(2)
            mutated_path = _mutate_path(original_path, all_paths, idx)

            if mutated_path == original_path:
                # Fallback: make it non-existent
                base, ext = os.path.splitext(original_path)
                mutated_path = base + '_WRONG' + ext

            # The full original text of the match
            full_original = match.group(0)
            full_replacement = match.group(1) + mutated_path + match.group(3)

            # Determine description
            if len(all_paths) > 1 and mutated_path in all_paths:
                desc = f"Swapped source path from '{original_path}' to '{mutated_path}' (another kernel's source)"
            elif mutated_path.endswith('.h') or mutated_path.endswith('.hpp'):
                desc = f"Changed source path from '{original_path}' to header file '{mutated_path}'"
            else:
                desc = f"Changed source path from '{original_path}' to wrong path '{mutated_path}'"

            candidates.append({
                "file_path": filepath,
                "bug_type": "source_path_wrong_for_kernel",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": match.end(),
                "original": full_original,
                "replacement": full_replacement,
                "description": desc
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]

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

    new_files[filepath] = new_content
    return new_files
