import re
import copy

BUG_FAMILY = {
    "family_id": "BF045",
    "bug_type": "duplicate_source_assignment_different_paths",
    "category": "kernel_source_paths",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["adf::source(", "source(k_"],
    "mutation_strategy": "Add a second adf::source() call for the same kernel object but pointing to a different (possibly nonexistent) file path, creating ambiguity or conflict in the ADF compiler's source resolution.",
    "repair_expectation": "Remove the duplicate adf::source() assignment, keeping only the single correct one.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about duplicate or conflicting source assignments for the same kernel.",
    "tags": [
        "adf_source",
        "compile_error",
        "duplicate_assignment",
        "graph_definition",
        "kernel_source_paths"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find all adf::source() or source(k_...) calls that can be duplicated with a different path."""
    candidates = []
    
    # Pattern to match source assignment lines like:
    # adf::source(kernel_obj) = "path/to/file.cc";
    # source(k_something) = "path/to/file.cc";
    # Also handle variations with whitespace and different quote styles
    pattern = re.compile(
        r'^([ \t]*)'                          # leading whitespace
        r'((?:adf::)?source\s*\(\s*'          # source( or adf::source(
        r'([A-Za-z_][A-Za-z0-9_.\[\]]*)'     # kernel object name
        r'\s*\)\s*=\s*"'                      # ) = "
        r'([^"]*)'                            # file path
        r'"\s*;)'                             # ";
        , re.MULTILINE
    )
    
    # Target files: graph headers (.h, .hpp) and graph sources (.cpp, .cc)
    graph_extensions = ('.h', '.hpp', '.cpp', '.cc', '.cxx')
    
    for file_path, content in project_files.items():
        # Check if file could be a graph header or source
        if not any(file_path.endswith(ext) for ext in graph_extensions):
            continue
        
        # Look for graph-related keywords to confirm it's a graph file
        is_graph_file = any(kw in content for kw in [
            'adf::graph', 'class graph', 'adf::source', 'source(k_',
            'adf::kernel', 'kernel::create'
        ])
        if not is_graph_file:
            continue
        
        for match in pattern.finditer(content):
            indent = match.group(1)
            full_statement = match.group(2)
            kernel_name = match.group(3)
            original_path = match.group(4)
            
            # Generate a different (nonexistent) path for the duplicate
            # Modify the filename to create a conflicting path
            if '/' in original_path:
                dir_part, file_part = original_path.rsplit('/', 1)
                fake_path = dir_part + "/alt_" + file_part
            else:
                fake_path = "alt_" + original_path
            
            # The duplicate line to insert
            duplicate_line = f'{indent}adf::source({kernel_name}) = "{fake_path}";'
            
            start = match.start()
            end = match.end()
            original_text = content[start:end]
            replacement_text = original_text + "\n" + duplicate_line
            
            candidate = {
                "file_path": file_path,
                "bug_type": "duplicate_source_assignment_different_paths",
                "category": "kernel_source_paths",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Added duplicate adf::source() call for kernel '{kernel_name}' "
                    f"with conflicting path '{fake_path}' (original: '{original_path}'), "
                    f"creating ambiguity in source resolution."
                )
            }
            candidates.append(candidate)
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply the mutation to produce a new copy of project_files."""
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
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
