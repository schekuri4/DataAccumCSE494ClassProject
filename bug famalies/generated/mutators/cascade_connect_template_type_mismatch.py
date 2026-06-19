import re
import copy

BUG_FAMILY = {
    "family_id": "BF141",
    "bug_type": "cascade_connect_template_type_mismatch",
    "category": "cascade_streams",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<cascade, cascade>",
        "connect<stream, cascade>",
        "connect<cascade, stream>",
        "connect<cascade>",
        "adf::connect"
    ],
    "mutation_strategy": "Replace a valid connect<cascade, cascade> template instantiation with connect<stream, cascade> or connect<cascade, stream>, creating a type mismatch between the port types and the connect template parameters.",
    "repair_expectation": "Restore the correct connect<cascade, cascade> template specialization matching both the output_cascade and input_cascade port types.",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or port type mismatch diagnostic from aiecompiler.",
    "tags": [
        "cascade",
        "cascade_streams",
        "connect",
        "stream",
        "template",
        "type_mismatch"
    ]
}


def _is_graph_file(file_path):
    """Heuristic to determine if a file is a graph header or graph source."""
    lower = file_path.lower()
    # Check for common graph file patterns
    if 'graph' in lower:
        return True
    # Also consider .h, .hpp, .cpp files that might be graph files
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match connect<cascade, cascade> with optional namespace prefixes
    # Handles: connect<cascade, cascade>, adf::connect<cascade, cascade>,
    # connect< cascade , cascade >, etc.
    pattern = re.compile(
        r'((?:adf\s*::\s*)?connect\s*<\s*)'  # group 1: prefix up to first template arg
        r'(cascade)'                            # group 2: first template param
        r'(\s*,\s*)'                           # group 3: comma with spaces
        r'(cascade)'                            # group 4: second template param
        r'(\s*>)'                              # group 5: closing >
    )

    single_arg_pattern = re.compile(
        r'((?:adf\s*::\s*)?connect\s*<\s*)'
        r'(cascade)'
        r'(\s*>)'
    )
    
    replacements = [
        ("stream", "cascade", "connect<stream, cascade>"),
        ("cascade", "stream", "connect<cascade, stream>"),
    ]
    
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        
        for match in pattern.finditer(content):
            start = match.start()
            end = match.end()
            original = match.group(0)
            
            # Generate two possible mutations for each match
            for first_param, second_param, desc_snippet in replacements:
                replacement = (
                    match.group(1) + first_param +
                    match.group(3) + second_param +
                    match.group(5)
                )
                
                # Skip if replacement is same as original (shouldn't happen but safety check)
                if replacement == original:
                    continue
                
                description = (
                    f"Replace '{original.strip()}' with '{replacement.strip()}' "
                    f"to create a cascade connect template type mismatch "
                    f"(changed to {desc_snippet})."
                )
                
                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": description,
                })

        for match in single_arg_pattern.finditer(content):
            original = match.group(0)
            replacement = match.group(1) + "stream" + match.group(3)
            if replacement == original:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    "Replace connect<cascade> with connect<stream> on an "
                    "existing cascade edge, creating a graph template/port "
                    "type mismatch."
                ),
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_project_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    
    # Verify the original text is still at the expected location
    if content[start:end] != original:
        # Fallback: try to find it and replace first occurrence
        idx = content.find(original)
        if idx == -1:
            # Cannot apply mutation, return unchanged
            return new_project_files
        start = idx
        end = idx + len(original)
    
    # Apply the mutation
    new_content = content[:start] + candidate["replacement"] + content[end:]
    new_project_files[file_path] = new_content
    
    return new_project_files
