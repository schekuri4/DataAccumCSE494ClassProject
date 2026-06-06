import re
import copy

BUG_FAMILY = {
    "family_id": "BF065",
    "bug_type": "kernel_out_port_index_overflow",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["connect<>(", ".out[", "k[0].out["],
    "mutation_strategy": "In a connect<> statement, reference a kernel output port index that exceeds the number of output ports defined in the kernel function (e.g., k[0].out[1] when the kernel only produces one output stream at out[0]).",
    "repair_expectation": "Correct the output port index to a valid value matching the kernel's actual output port count.",
    "validation_signal": "WSL Vitis/AIE compile failure with invalid port index or port not found error during ADF graph compilation.",
    "tags": ["connect", "graph_endpoint_indices", "index_overflow", "kernel_port", "out_port"]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for AIE graph files
    if 'graph' in lower:
        return True
    # Also consider .h and .cpp files that might be graph files
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def find_mutation_candidates(project_files):
    """Find all .out[N] references within connect<> statements that can be mutated."""
    candidates = []
    
    # Pattern to match kernel output port references like: something.out[N]
    # within or near connect<> statements
    # We look for .out[<number>] patterns
    out_port_pattern = re.compile(
        r'(\.out\[)(\d+)(\])'
    )
    
    # Pattern to detect connect<> context (line contains connect)
    connect_pattern = re.compile(r'connect\s*<[^>]*>\s*\(')
    
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        
        lines = content.split('\n')
        offset = 0
        
        for line_idx, line in enumerate(lines):
            # Check if this line contains a connect<> call or is part of one
            # Also check lines that have .out[ which might be in connect context
            has_connect = connect_pattern.search(line) is not None
            has_out = '.out[' in line
            
            if has_out:
                # Look for connect context in nearby lines or same line
                in_connect_context = has_connect
                if not in_connect_context:
                    # Check a few lines above for multi-line connect statements
                    for check_idx in range(max(0, line_idx - 3), line_idx):
                        if connect_pattern.search(lines[check_idx]):
                            in_connect_context = True
                            break
                
                if not in_connect_context:
                    # Still consider it if the line looks like it's part of graph connectivity
                    # (e.g., contains kernel-like variable access with .out[)
                    if not re.search(r'\w+(\[\d+\])?\s*\.out\[', line):
                        offset += len(line) + 1
                        continue
                
                # Find all .out[N] occurrences in this line
                for match in out_port_pattern.finditer(line):
                    current_index = int(match.group(2))
                    # Create overflow: increment the index by 1
                    new_index = current_index + 1
                    
                    original_text = match.group(0)  # e.g., .out[0]
                    replacement_text = f".out[{new_index}]"
                    
                    abs_start = offset + match.start()
                    abs_end = offset + match.end()
                    
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "kernel_out_port_index_overflow",
                        "category": "graph_endpoint_indices",
                        "start": abs_start,
                        "end": abs_end,
                        "original": original_text,
                        "replacement": replacement_text,
                        "description": (
                            f"Overflow kernel output port index from {current_index} to "
                            f"{new_index} in '{filepath}' at line {line_idx + 1}. "
                            f"This references a port index that likely exceeds the kernel's "
                            f"actual number of output ports."
                        )
                    })
            
            offset += len(line) + 1
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate["file_path"]
    content = new_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    
    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text != original:
        # Fallback: try to find and replace first occurrence
        idx = content.find(original)
        if idx == -1:
            # Cannot apply mutation, return unchanged
            return new_files
        start = idx
        end = idx + len(original)
    
    # Apply the mutation
    new_content = content[:start] + candidate["replacement"] + content[end:]
    new_files[filepath] = new_content
    
    return new_files
