import re
import copy

BUG_FAMILY = {
    "family_id": "BF048",
    "bug_type": "missing_header_include_path_in_source",
    "category": "kernel_source_paths",
    "target_files": [
        "kernel source",
        "kernel header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include",
        "\"kernels/",
        "\"../include/",
        "adf.h",
        "aie_api/"
    ],
    "mutation_strategy": "Modify an #include directive in the kernel source file to use an incorrect relative path to the kernel header (e.g., change '#include \"kernels/fir.h\"' to '#include \"fir.h\"' or '#include \"../kernels/fir.h\"') without corresponding include path flags.",
    "repair_expectation": "Restore the correct relative include path or add the appropriate include directory to the build configuration.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'file not found' error on the #include directive.",
    "tags": [
        "compile_error",
        "include_path",
        "kernel_header",
        "kernel_source_paths",
        "relative_path"
    ]
}


def _is_kernel_source(file_path):
    """Heuristic to identify kernel source files (C/C++ sources)."""
    extensions = ('.cpp', '.cc', '.c', '.cxx')
    return file_path.lower().endswith(extensions)


def _is_kernel_header(file_path):
    """Heuristic to identify kernel header files."""
    extensions = ('.h', '.hpp', '.hxx')
    return file_path.lower().endswith(extensions)


def _generate_incorrect_path(original_path):
    """Generate an incorrect relative path from the original include path."""
    # Strategy: strip directory components to make it just the filename,
    # or prepend an incorrect relative prefix
    
    # Extract just the filename
    parts = original_path.replace('\\', '/').split('/')
    
    if len(parts) > 1:
        # Has directory components - strip them to just filename
        filename_only = parts[-1]
        return filename_only
    else:
        # Already just a filename - prepend an incorrect directory
        return "../wrong_path/" + original_path


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match #include directives with quoted paths that have directory components
    # Focus on match_targets: "kernels/", "../include/", "aie_api/"
    include_pattern = re.compile(
        r'(#\s*include\s*")((?:[^"]*/)([^"]+))"'
    )
    
    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path) and not _is_kernel_header(file_path):
            continue
        
        for match in include_pattern.finditer(content):
            full_match = match.group(0)
            prefix = match.group(1)  # #include "
            rel_path = match.group(2)  # e.g., kernels/fir.h
            filename = match.group(3)  # e.g., fir.h
            
            # Check if this matches any of our target patterns
            is_target = False
            for target in BUG_FAMILY["match_targets"]:
                if target == "#include":
                    continue  # Too generic alone
                if target.strip('"') in rel_path:
                    is_target = True
                    break
            
            # Also consider any include with a directory path in kernel sources
            if not is_target and '/' in rel_path and _is_kernel_source(file_path):
                is_target = True
            
            if not is_target:
                continue
            
            incorrect_path = _generate_incorrect_path(rel_path)
            
            if incorrect_path == rel_path:
                continue
            
            replacement = prefix + incorrect_path + '"'
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": (
                    f"Changed include path from '{rel_path}' to '{incorrect_path}' "
                    f"in {file_path}, making the header unfindable without correct "
                    f"include path flags."
                )
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    
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
