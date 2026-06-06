import re
import copy

BUG_FAMILY = {
    "family_id": "BF091",
    "bug_type": "rtp_wrong_direction_specifier",
    "category": "rtp_parameters",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<parameter>(",
        "adf::parameter::create",
        "port<direction::in>",
        "port<direction::out>",
        "port<direction::inout>"
    ],
    "mutation_strategy": "Change the direction of an RTP port declaration from direction::in to direction::out (or vice versa) while keeping the update() call unchanged, causing a compile-time mismatch between the port direction and the API used to drive it.",
    "repair_expectation": "Restore the correct direction specifier (e.g., direction::in for an input RTP that is updated via graph::update()).",
    "validation_signal": "WSL Vitis/AIE compile failure with error indicating direction mismatch or invalid connection on RTP port.",
    "tags": ["compile_time", "direction", "graph", "port", "rtp", "rtp_parameters"]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or graph source files."""
    lower = filepath.lower()
    # Common patterns for graph files in AIE projects
    if 'graph' in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might be graph files
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match port<direction::in>, port<direction::out>, port<direction::inout>
    # Handles optional whitespace and adf:: namespace prefix
    pattern = re.compile(
        r'((?:adf::)?port\s*<\s*(?:adf::)?direction\s*::\s*)(in|out|inout)(\s*>)'
    )
    
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        
        for match in pattern.finditer(content):
            original_direction = match.group(2)
            
            # Determine replacement direction
            if original_direction == "in":
                new_direction = "out"
            elif original_direction == "out":
                new_direction = "in"
            elif original_direction == "inout":
                # For inout, flip to in (arbitrary but deterministic)
                new_direction = "in"
            else:
                continue
            
            original_text = match.group(0)
            replacement_text = match.group(1) + new_direction + match.group(3)
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": filepath,
                "bug_type": "rtp_wrong_direction_specifier",
                "category": "rtp_parameters",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed RTP port direction from '{original_direction}' to "
                    f"'{new_direction}' in '{filepath}' at position {start}-{end}, "
                    f"causing a direction mismatch with the update()/read() API calls."
                )
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate["file_path"]
    content = new_project_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is still at the expected position
    actual_text = content[start:end]
    if actual_text != original:
        # Fallback: try to find and replace the first occurrence
        idx = content.find(original)
        if idx == -1:
            # Cannot apply mutation; return unchanged
            return new_project_files
        start = idx
        end = idx + len(original)
    
    # Apply the mutation
    new_content = content[:start] + replacement + content[end:]
    new_project_files[filepath] = new_content
    
    return new_project_files
