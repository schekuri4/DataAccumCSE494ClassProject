import re
import copy

BUG_FAMILY = {
    "family_id": "BF003",
    "bug_type": "case_sensitive_kernel_header_include",
    "category": "include_headers",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "reference_missing_file",
    "match_targets": [
        '#include "kernels.h"',
        '#include "kernel.h"',
        '#include "fir_filter.h"'
    ],
    "mutation_strategy": "Change the case of the kernel header filename in the #include directive (e.g., #include \"Kernels.h\" instead of #include \"kernels.h\") so that on case-sensitive Linux/WSL filesystems the file is not found, while the actual file exists with lowercase naming.",
    "repair_expectation": "Correct the case of the included filename to match the actual file on disk exactly.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'fatal error: Kernels.h: No such file or directory'.",
    "tags": [
        "case_sensitivity",
        "file_not_found",
        "include_headers",
        "kernel_header"
    ]
}


def _mutate_filename_case(filename):
    """Change the case of the first character of the filename to create a case mismatch."""
    if not filename:
        return filename
    # Split into basename and extension
    dot_idx = filename.rfind('.')
    if dot_idx > 0:
        basename = filename[:dot_idx]
        ext = filename[dot_idx:]
    else:
        basename = filename
        ext = ""
    
    # Capitalize the first letter of the basename
    if basename[0].islower():
        mutated_basename = basename[0].upper() + basename[1:]
    elif basename[0].isupper():
        mutated_basename = basename[0].lower() + basename[1:]
    else:
        # If first char is not a letter, try capitalizing subsequent chars
        mutated_basename = basename
        for i, c in enumerate(basename):
            if c.isalpha():
                if c.islower():
                    mutated_basename = basename[:i] + c.upper() + basename[i+1:]
                else:
                    mutated_basename = basename[:i] + c.lower() + basename[i+1:]
                break
    
    # If mutation didn't change anything, try uppercasing entire basename
    if mutated_basename == basename:
        mutated_basename = basename.upper()
    
    return mutated_basename + ext


def _is_graph_file(file_path):
    """Heuristic to identify graph header or graph source files."""
    lower_path = file_path.lower()
    # Check for common graph file patterns
    if 'graph' in lower_path:
        return True
    # Also consider any .h or .cpp file that might be a graph file
    # since in AIE projects, graph files include kernel headers
    if lower_path.endswith(('.h', '.hpp', '.cpp', '.cc', '.c')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match #include "something.h" style includes
    include_pattern = re.compile(r'#include\s+"([^"]+)"')
    
    # Known kernel header filenames (lowercase) that we target
    target_basenames = [
        'kernels.h', 'kernel.h', 'fir_filter.h'
    ]
    
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        
        for match in include_pattern.finditer(content):
            included_file = match.group(1)
            # Get just the basename for comparison
            basename = included_file.split('/')[-1] if '/' in included_file else included_file
            
            # Check if this matches one of our target patterns (case-insensitive match to find lowercase originals)
            if basename.lower() in target_basenames:
                # Only mutate if the current include is already in the "correct" lowercase form
                # (we want to introduce the bug, not fix one)
                if basename == basename.lower():
                    # Create mutated filename
                    if '/' in included_file:
                        prefix = included_file[:included_file.rfind('/') + 1]
                        mutated_name = prefix + _mutate_filename_case(basename)
                    else:
                        mutated_name = _mutate_filename_case(basename)
                    
                    original_text = match.group(0)
                    replacement_text = '#include "' + mutated_name + '"'
                    
                    # Ensure mutation actually changes something
                    if original_text == replacement_text:
                        continue
                    
                    start = match.start()
                    end = match.end()
                    
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "case_sensitive_kernel_header_include",
                        "category": "include_headers",
                        "start": start,
                        "end": end,
                        "original": original_text,
                        "replacement": replacement_text,
                        "description": (
                            f"Changed case of kernel header include from "
                            f'"{included_file}" to "{mutated_name}" to cause '
                            f"file-not-found error on case-sensitive filesystems."
                        )
                    })
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
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
    
    new_files[file_path] = new_content
    return new_files
