import re
import copy

BUG_FAMILY = {
    "family_id": "BF122",
    "bug_type": "window_size_not_multiple_of_element_size",
    "category": "window_interfaces",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<window<",
        "window_size",
        "input_plio",
        "adf::connect"
    ],
    "mutation_strategy": "Modify the window size in a connect<window<N>> declaration so that N is not a multiple of the element type's byte size (e.g., use window<127> for int32 elements which require multiples of 4), causing an invalid window configuration.",
    "repair_expectation": "Correct the window size to be an exact multiple of the element type size in bytes.",
    "validation_signal": "WSL Vitis/AIE compile failure with window size alignment or divisibility error during graph compilation.",
    "tags": [
        "alignment",
        "compile_time",
        "connect",
        "graph",
        "window_interfaces",
        "window_size"
    ]
}


def _is_graph_header(file_path):
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Graph headers are typically .h or .hpp files, often containing 'graph' in name
    if lower.endswith(('.h', '.hpp', '.hh', '.hxx')):
        return True
    return False


def _infer_element_size(content, match_start):
    """Try to infer element type size from context around the window declaration."""
    # Look backwards and forwards for type hints
    # Common AIE types and their sizes
    type_sizes = {
        'int8': 1, 'uint8': 1, 'int8_t': 1, 'uint8_t': 1,
        'int16': 2, 'uint16': 2, 'int16_t': 2, 'uint16_t': 2,
        'cint16': 4,
        'int32': 4, 'uint32': 4, 'int32_t': 4, 'uint32_t': 4, 'float': 4,
        'cint32': 8, 'cfloat': 8, 'int64': 8, 'uint64_t': 8, 'int64_t': 8, 'double': 8,
    }
    
    # Search in a window around the match for type references
    context_start = max(0, match_start - 500)
    context_end = min(len(content), match_start + 500)
    context = content[context_start:context_end]
    
    # Look for type patterns like port<input> or kernel type declarations
    for type_name, size in sorted(type_sizes.items(), key=lambda x: -len(x[0])):
        if type_name in context:
            return size
    
    # Default assumption: int32 (4 bytes)
    return 4


def _make_non_multiple(value, element_size):
    """Create a value that is NOT a multiple of element_size but is close to original."""
    if element_size <= 1:
        # For byte-sized elements, any size works, so we can't really break it
        # Use a prime number that's unlikely to be valid for other reasons
        return value - 1 if value > 1 else value + 1
    
    # Find a nearby value that's not a multiple of element_size
    candidate = value - 1
    if candidate > 0 and candidate % element_size != 0:
        return candidate
    
    candidate = value + 1
    if candidate % element_size != 0:
        return candidate
    
    # Try subtracting more
    for offset in range(1, element_size):
        candidate = value - offset
        if candidate > 0 and candidate % element_size != 0:
            return candidate
    
    # Fallback
    return value + 1


def find_mutation_candidates(project_files):
    """Find window size declarations in graph headers that can be mutated."""
    candidates = []
    
    # Pattern to match window<N> in connect declarations or similar contexts
    # Matches: window<128>, window< 256 >, etc.
    window_pattern = re.compile(r'window\s*<\s*(\d+)\s*>')
    
    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue
        
        # Check if file has any of the match targets suggesting it's a graph file with windows
        has_relevant_content = any(
            target in content for target in ['connect<window<', 'window_size', 'adf::connect', 'connect<']
        )
        if not has_relevant_content:
            continue
        
        for match in window_pattern.finditer(content):
            window_size_str = match.group(1)
            window_size = int(window_size_str)
            
            if window_size <= 0:
                continue
            
            # Infer element size from context
            element_size = _infer_element_size(content, match.start())
            
            # Only mutate if current value IS a valid multiple
            if window_size % element_size != 0:
                continue
            
            # Compute a bad window size
            bad_size = _make_non_multiple(window_size, element_size)
            
            # Build the replacement string preserving whitespace
            original_text = match.group(0)
            # Replace just the number inside window< >
            replacement_text = re.sub(r'(\d+)', str(bad_size), original_text, count=1)
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_size_not_multiple_of_element_size",
                "category": "window_interfaces",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed window size from {window_size} to {bad_size} "
                    f"(not a multiple of element size {element_size} bytes) "
                    f"in '{file_path}', causing invalid window configuration."
                )
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply a mutation candidate to produce a new set of project files."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
