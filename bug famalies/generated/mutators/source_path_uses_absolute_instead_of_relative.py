import re
import copy

BUG_FAMILY = {
    "family_id": "BF046",
    "bug_type": "source_path_uses_absolute_instead_of_relative",
    "category": "kernel_source_paths",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::source(",
        "\"/home/",
        "\"/opt/",
        "\"C:\\\\"
    ],
    "mutation_strategy": "Replace the relative path in adf::source() with an absolute filesystem path (e.g., '/home/user/project/src/kernels/fir.cc') that is not portable and may not exist in the build environment.",
    "repair_expectation": "Replace the absolute path with the correct project-relative path.",
    "validation_signal": "WSL Vitis/AIE compile failure because the absolute path does not resolve correctly in the build system or does not exist.",
    "tags": [
        "absolute_path",
        "adf_source",
        "build_error",
        "kernel_source_paths",
        "portability"
    ]
}


def _is_graph_file(file_path):
    """Heuristic to identify graph header or graph source files."""
    lower = file_path.lower()
    # Common patterns for graph files in AIE projects
    if 'graph' in lower:
        return True
    # Also consider .h and .cpp files that might contain graph definitions
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def _is_already_absolute(path_str):
    """Check if a path string is already absolute."""
    return path_str.startswith('/') or (len(path_str) >= 3 and path_str[1] == ':' and path_str[2] == '\\')


def _make_absolute_path(relative_path):
    """Convert a relative path to a fake absolute path for mutation."""
    # Strip any leading ./ or ../
    clean = relative_path.lstrip('.').lstrip('/').lstrip('\\')
    return '/home/user/project/src/' + clean


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match adf::source() calls with a string argument containing a relative path
    # Matches: adf::source("path/to/file.cc") or source("path/to/file.cc")
    pattern = re.compile(
        r'((?:adf::)?source\s*\(\s*")'  # prefix: adf::source(" 
        r'([^"]+)'                        # the path string
        r'("\s*\))'                        # closing ")
    )
    
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        
        for match in pattern.finditer(content):
            path_value = match.group(2)
            
            # Only mutate if the path is currently relative (not already absolute)
            if _is_already_absolute(path_value):
                continue
            
            absolute_path = _make_absolute_path(path_value)
            
            original_full = match.group(0)
            replacement_full = match.group(1) + absolute_path + match.group(3)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": original_full,
                "replacement": replacement_full,
                "description": (
                    f"Replace relative kernel source path '{path_value}' with "
                    f"absolute path '{absolute_path}' in adf::source() call, "
                    f"making it non-portable and likely to fail in different build environments."
                )
            })
    
    return candidates


def apply_mutation(project_files, candidate):
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
