import re
import copy

BUG_FAMILY = {
    "family_id": "BF051",
    "bug_type": "graph_connections__connect_template_type_mismatch",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<window<",
        "connect<stream",
        "connect<pktstream",
        "adf::connect"
    ],
    "mutation_strategy": "Replace the correct connect template parameter with an incompatible type, e.g., change connect<window<256>> to connect<stream> when the kernel port expects a window interface, or vice versa. This creates a template type mismatch between the port declaration and the connection instantiation.",
    "repair_expectation": "Restore the connect template parameter to match the declared kernel port interface type (window, stream, or pktstream).",
    "validation_signal": "WSL Vitis/AIE compile failure with error indicating port type mismatch or incompatible connection template arguments.",
    "tags": ["connect", "graph_connections", "stream", "template", "type_mismatch", "window"]
}


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files, or contain 'graph' in name."""
    lower = file_path.lower()
    if lower.endswith('.h') or lower.endswith('.hpp'):
        return True
    if 'graph' in lower:
        return True
    return False


def _generate_replacement(original_type_str):
    """Given the original connect template type string, produce an incompatible replacement."""
    stripped = original_type_str.strip()
    
    # Detect what type it is and replace with something incompatible
    if re.match(r'window\s*<', stripped):
        # Replace window<...> with stream
        return "stream"
    elif re.match(r'pktstream', stripped):
        # Replace pktstream with stream
        return "stream"
    elif re.match(r'stream', stripped):
        # Replace stream with window<256>
        return "window<256>"
    else:
        # Default: replace with stream
        return "stream"


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match connect< TYPE > or connect< TYPE, TYPE >
    # We capture the full connect<...> expression
    # Handles: connect<window<N>>, connect<stream>, connect<pktstream, pktstream>, adf::connect<...>
    pattern = re.compile(
        r'((?:adf\s*::\s*)?connect\s*<\s*)'  # group 1: prefix up to first type
        r'('                                    # group 2: the first template type argument
        r'window\s*<\s*\d+\s*>'                # window<N>
        r'|pktstream'                           # pktstream
        r'|stream'                              # stream
        r')'
    )
    
    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue
        
        for match in pattern.finditer(content):
            prefix = match.group(1)
            original_type = match.group(2)
            
            replacement_type = _generate_replacement(original_type)
            
            # Skip if replacement is same as original
            if replacement_type.strip() == original_type.strip():
                continue
            
            full_original = prefix + original_type
            full_replacement = prefix + replacement_type
            
            start = match.start()
            end = match.start() + len(full_original)
            
            candidate = {
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": full_original,
                "replacement": full_replacement,
                "description": (
                    f"Replace connect template type '{original_type}' with "
                    f"incompatible type '{replacement_type}' to create a "
                    f"port type mismatch in graph connection."
                )
            }
            candidates.append(candidate)
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: use string replacement for first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    
    new_files[file_path] = new_content
    return new_files
