import re
import copy

BUG_FAMILY = {
    "family_id": "BF050",
    "bug_type": "omitted_source_assignment_for_kernel",
    "category": "kernel_source_paths",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::source(",
        "source(k_",
        "kernel::create",
        "runtime<ratio>"
    ],
    "mutation_strategy": "Comment out or delete the adf::source() line for one kernel object in the graph constructor while keeping the kernel::create and connection statements intact, leaving the ADF compiler without a source file for that kernel.",
    "repair_expectation": "Add back the adf::source() assignment with the correct path for the kernel object.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating no source file specified for the kernel or kernel has no implementation.",
    "tags": [
        "adf_source",
        "compile_error",
        "graph_constructor",
        "kernel_source_paths",
        "missing_assignment"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find all adf::source() lines that can be commented out."""
    candidates = []
    
    # Pattern to match adf::source() or source() calls for kernel objects
    # Matches lines like:
    #   adf::source(k_something) = "path/to/file.cc";
    #   source(k_something) = "path/to/file.cc";
    #   adf::source(kernelName) = "file.cpp";
    source_pattern = re.compile(
        r'^([ \t]*)((?:adf::)?source\s*\([^)]+\)\s*=\s*"[^"]*"\s*;)[ \t]*$',
        re.MULTILINE
    )
    
    for file_path, content in project_files.items():
        # Target graph headers (.h, .hpp) and graph source files (.cpp, .cc)
        lower_path = file_path.lower()
        is_graph_file = False
        
        # Check if file likely contains graph definition
        if any(ext in lower_path for ext in ['.h', '.hpp', '.cpp', '.cc']):
            # Verify it contains relevant graph/kernel content
            if ('kernel::create' in content or 'adf::kernel::create' in content or
                'source(' in content):
                is_graph_file = True
        
        if not is_graph_file:
            continue
        
        for match in source_pattern.finditer(content):
            indent = match.group(1)
            source_line = match.group(2)
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            
            # Build description
            description = (
                f"Comment out the adf::source() assignment '{source_line.strip()}' "
                f"to leave the kernel without a specified source file."
            )
            
            candidate = {
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": f"{indent}// {source_line}  // BF050: source assignment removed",
                "description": description
            }
            candidates.append(candidate)
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply the mutation by commenting out the adf::source() line."""
    new_project_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    original_content = new_project_files[file_path]
    
    original_text = candidate["original"]
    replacement_text = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]
    
    # Verify the original text is at the expected position
    if original_content[start:end] == original_text:
        # Replace using position
        new_content = original_content[:start] + replacement_text + original_content[end:]
    else:
        # Fallback: replace first occurrence of the original text
        new_content = original_content.replace(original_text, replacement_text, 1)
    
    new_project_files[file_path] = new_content
    return new_project_files
