import re
import copy

BUG_FAMILY = {
    "family_id": "BF057",
    "bug_type": "port_index_out_of_range",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [".in[0]", ".in[1]", ".out[0]", ".out[1]", "connect<"],
    "mutation_strategy": "Change a port index to a value beyond the number of ports declared for that kernel, e.g., use .in[2] when the kernel only has 2 input ports (indices 0 and 1), or .out[1] when only one output port exists.",
    "repair_expectation": "Correct the port index to a valid value within the range of declared ports for that kernel.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about port index out of bounds or no such port.",
    "tags": ["connect", "graph_connections", "kernel_port", "out_of_range", "port_index"]
}


def _is_graph_header(file_path):
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Typical graph headers: .h or .hpp files with 'graph' in name or path
    if lower.endswith(('.h', '.hpp')):
        if 'graph' in lower:
            return True
    # Also consider any header that contains graph class definitions
    return False


def _is_likely_graph_header_by_content(content):
    """Check if file content looks like a graph header."""
    # Look for adf::graph or graph class patterns, or connect< usage
    if re.search(r'\b(adf::graph|class\s+\w+\s*:\s*public\s+(adf::)?graph)', content):
        return True
    if 'connect<' in content and ('.in[' in content or '.out[' in content):
        return True
    return False


def _find_graph_headers(project_files):
    """Find all files that are likely graph headers."""
    results = []
    for path, content in project_files.items():
        if _is_graph_header(path) or _is_likely_graph_header_by_content(content):
            results.append(path)
    return results


def find_mutation_candidates(project_files):
    candidates = []
    
    graph_headers = _find_graph_headers(project_files)
    
    # Pattern to match port index references like .in[0], .in[1], .out[0], .out[1]
    port_pattern = re.compile(r'\.(in|out)\[(\d+)\]')
    
    for file_path in graph_headers:
        content = project_files[file_path]
        
        for match in port_pattern.finditer(content):
            port_type = match.group(1)  # 'in' or 'out'
            current_index = int(match.group(2))
            
            # Compute a new index that is out of range
            # Strategy: increment by 2 to make it clearly out of range
            # If current is 0 or 1, use current + 2 (so .in[0] -> .in[2], .in[1] -> .in[3])
            new_index = current_index + 2
            
            original_text = match.group(0)  # e.g., ".in[0]"
            replacement_text = f".{port_type}[{new_index}]"
            
            start = match.start()
            end = match.end()
            
            description = (
                f"Change port index from .{port_type}[{current_index}] to "
                f".{port_type}[{new_index}], which is likely beyond the number "
                f"of declared {port_type}put ports for the kernel."
            )
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "port_index_out_of_range",
                "category": "graph_connections",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": description
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
