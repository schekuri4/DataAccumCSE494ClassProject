import re
import copy

BUG_FAMILY = {
    "family_id": "BF004",
    "bug_type": "wrong_relative_path_for_kernel_include",
    "category": "include_headers",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "reference_missing_file",
    "match_targets": [
        "#include \"../kernels/",
        "#include \"./src/",
        "#include \"kernels/"
    ],
    "mutation_strategy": "Alter the relative path in a kernel header include to point to a non-existent directory (e.g., change #include \"../src/kernels/fir.h\" to #include \"../kernels/fir.h\" or add an extra directory level), simulating a common path misconfiguration in AIE projects.",
    "repair_expectation": "Fix the relative include path to correctly resolve to the kernel header file location relative to the including file.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'fatal error: <wrong_path>: No such file or directory'.",
    "tags": [
        "include_headers",
        "include_path",
        "project_structure",
        "relative_path"
    ]
}

# Pattern to match #include with a relative path containing directory separators
_INCLUDE_PATTERN = re.compile(
    r'#include\s+"((?:\.\./|\./)?(?:[a-zA-Z0-9_\-]+/)+[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_]+)"'
)

# Files that look like graph headers or graph sources
_GRAPH_FILE_PATTERN = re.compile(r'graph', re.IGNORECASE)


def _is_target_file(file_path):
    """Check if file is likely a graph header or graph source."""
    lower = file_path.lower()
    # Check for graph-related naming or common AIE graph file patterns
    if 'graph' in lower:
        return True
    # Also consider .h and .cpp files that contain graph-like content
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.c')):
        return True
    return False


def _mutate_path(original_path):
    """Alter a relative path to point to a non-existent directory."""
    # Strategy 1: If path starts with ../, add an extra ../ level
    if original_path.startswith("../"):
        return "../../" + original_path[3:]
    
    # Strategy 2: If path starts with ./, change to ../
    if original_path.startswith("./"):
        return "../" + original_path[2:]
    
    # Strategy 3: Insert a bogus directory level after the first directory component
    parts = original_path.split("/")
    if len(parts) >= 2:
        # Insert "nonexistent" directory after first component
        parts.insert(1, "nonexistent")
        return "/".join(parts)
    
    # Strategy 4: Prepend ../
    return "../" + original_path


def find_mutation_candidates(project_files):
    candidates = []
    
    for file_path, content in project_files.items():
        if not _is_target_file(file_path):
            continue
        
        for match in _INCLUDE_PATTERN.finditer(content):
            include_path = match.group(1)
            
            # Check if this matches any of our target patterns
            full_directive = match.group(0)
            is_target = False
            for target in BUG_FAMILY["match_targets"]:
                if target in full_directive:
                    is_target = True
                    break
            
            # Also accept any relative path include with directory components
            # that looks like a kernel include
            if not is_target:
                lower_path = include_path.lower()
                if any(kw in lower_path for kw in ['kernel', 'src', 'include', 'aie']):
                    is_target = True
                elif '/' in include_path and (include_path.startswith('../') or 
                                               include_path.startswith('./') or
                                               not include_path.startswith('/')):
                    is_target = True
            
            if not is_target:
                continue
            
            mutated_path = _mutate_path(include_path)
            
            # Ensure mutation actually changes something
            if mutated_path == include_path:
                continue
            
            original_text = f'#include "{include_path}"'
            replacement_text = f'#include "{mutated_path}"'
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Altered relative include path from \"{include_path}\" to "
                    f"\"{mutated_path}\" to simulate a path misconfiguration in AIE project."
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
