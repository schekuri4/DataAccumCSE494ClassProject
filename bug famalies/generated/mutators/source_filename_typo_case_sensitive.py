import re
import copy


BUG_FAMILY = {
    "family_id": "BF043",
    "bug_type": "source_filename_typo_case_sensitive",
    "category": "kernel_source_paths",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::source(",
        "kernel::create",
        "\"src/",
        "\"kernels/"
    ],
    "mutation_strategy": "Introduce a case-sensitivity error in the kernel filename within adf::source() (e.g., 'FIR_filter.cc' instead of 'fir_filter.cc'), which will fail on case-sensitive Linux/WSL filesystems.",
    "repair_expectation": "Correct the filename casing to exactly match the file on disk.",
    "validation_signal": "WSL Vitis/AIE compile failure with file-not-found error due to case mismatch on case-sensitive filesystem.",
    "tags": [
        "adf_source",
        "case_sensitivity",
        "filename_typo",
        "kernel_source_paths",
        "linux_filesystem"
    ]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or graph source files."""
    lower = filepath.lower()
    # Common patterns for graph files in AIE projects
    if "graph" in lower:
        return True
    # Also consider files with typical graph-related extensions in src directories
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def _introduce_case_error(filename):
    """Introduce a case-sensitivity error in a filename (not the path directories).
    Strategy: toggle the case of the first alphabetic character in the basename."""
    # Split into directory part and basename
    if '/' in filename:
        last_slash = filename.rfind('/')
        dir_part = filename[:last_slash + 1]
        basename = filename[last_slash + 1:]
    else:
        dir_part = ""
        basename = filename

    if not basename:
        return None

    # Find first alphabetic character in basename and toggle its case
    new_basename = list(basename)
    mutated = False
    for i, ch in enumerate(new_basename):
        if ch.isalpha():
            if ch.islower():
                new_basename[i] = ch.upper()
            else:
                new_basename[i] = ch.lower()
            mutated = True
            break

    if not mutated:
        return None

    return dir_part + ''.join(new_basename)


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match adf::source() calls with string arguments containing filenames
    # Also match patterns with "src/" or "kernels/" path prefixes
    source_pattern = re.compile(
        r'(adf::source\s*\(\s*\w+\s*\)\s*=\s*"([^"]+)")'
        r'|'
        r'(source\s*\(\s*\w+\s*\)\s*=\s*"([^"]+)")'
    )

    # More general pattern for string literals containing kernel source paths
    path_string_pattern = re.compile(
        r'"((?:src/|kernels/)[^"]*\.[a-zA-Z]+)"'
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        lines = content.split('\n')

        for line_idx, line in enumerate(lines):
            # Check if line contains any of our match targets
            has_match_target = any(target in line for target in [
                "adf::source(", "kernel::create", '"src/', '"kernels/'
            ])
            if not has_match_target:
                continue

            # Try to find source path assignments like: adf::source(k) = "src/kernel.cc";
            for m in re.finditer(r'"((?:[^"]*/)?)([^"/]+\.[a-zA-Z]+)"', line):
                full_path = m.group(1) + m.group(2)
                # Only mutate if it looks like a source file path
                if not any(ext in m.group(2) for ext in ['.cc', '.cpp', '.c', '.h', '.hpp']):
                    continue

                mutated_path = _introduce_case_error(full_path)
                if mutated_path is None or mutated_path == full_path:
                    continue

                original_str = '"' + full_path + '"'
                replacement_str = '"' + mutated_path + '"'

                # Find exact position in the line
                col_start = line.find(original_str)
                if col_start == -1:
                    continue

                # Calculate absolute character positions
                abs_start = sum(len(lines[i]) + 1 for i in range(line_idx)) + col_start
                abs_end = abs_start + len(original_str)

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "source_filename_typo_case_sensitive",
                    "category": "kernel_source_paths",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_str,
                    "replacement": replacement_str,
                    "description": (
                        f"Introduced case-sensitivity error in kernel source filename: "
                        f"{original_str} -> {replacement_str}. This will cause a "
                        f"file-not-found error on case-sensitive filesystems (Linux/WSL)."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a mutation candidate to produce a new set of project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    content = new_files[filepath]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
