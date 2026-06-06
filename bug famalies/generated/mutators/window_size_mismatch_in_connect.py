import re
import copy

BUG_FAMILY = {
    "family_id": "BF058",
    "bug_type": "window_size_mismatch_in_connect",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["connect<window<", "window_size", "margin"],
    "mutation_strategy": "Change the window size in a connect<window<N>> template to a value that does not match the kernel's declared input_window or output_window size, or use a non-multiple-of-required-alignment value. For example, if the kernel expects window<512>, change the connect to window<256>.",
    "repair_expectation": "Set the window size in the connect template to match the kernel port's expected window size.",
    "validation_signal": "WSL Vitis/AIE compile failure with window size mismatch or incompatible buffer size error.",
    "tags": ["buffer", "connect", "graph_connections", "mismatch", "window_size"]
}


def _is_graph_header(file_path):
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Typical graph headers: .h or .hpp files with 'graph' in name or path
    if lower.endswith(('.h', '.hpp')):
        if 'graph' in lower:
            return True
    # Also consider any header that might contain connect<window< patterns
    return False


def _mutate_window_size(original_size):
    """Generate a mismatched window size."""
    size = int(original_size)
    if size > 128:
        # Halve it
        return str(size // 2)
    elif size > 0:
        # Double it
        return str(size * 2)
    else:
        return "128"


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match connect<window<N>> or connect<window<N, M>> (with optional margin)
    # Also matches variations like connect< window< N > >
    pattern = re.compile(
        r'(connect\s*<\s*window\s*<\s*)(\d+)(\s*(?:,\s*\d+\s*)?>)'
    )
    
    for file_path, content in project_files.items():
        # Check if this looks like a graph header
        lower = file_path.lower()
        is_header = lower.endswith(('.h', '.hpp', '.hh'))
        has_graph_indicator = 'graph' in lower or 'connect<window<' in content.lower()
        
        if not (is_header and has_graph_indicator):
            # Also accept .cpp files that contain graph definitions with connect<window<
            if not (lower.endswith('.cpp') and 'connect<window<' in content.lower() and 'graph' in content.lower()):
                continue
        
        for match in pattern.finditer(content):
            original_size = match.group(2)
            new_size = _mutate_window_size(original_size)
            
            if new_size == original_size:
                continue
            
            start = match.start(2)
            end = match.end(2)
            
            original_text = match.group(0)
            replacement_text = match.group(1) + new_size + match.group(3)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_size_mismatch_in_connect",
                "category": "graph_connections",
                "start": match.start(0),
                "end": match.end(0),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed window size in connect template from {original_size} to {new_size} "
                    f"to create a window size mismatch with the kernel's expected buffer size."
                )
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
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    
    new_project_files[file_path] = new_content
    return new_project_files
