import re
import copy

BUG_FAMILY = {
    "family_id": "BF070",
    "bug_type": "negative_or_typed_wrong_index_in_endpoint",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [".in[", ".out[", "k[", "plin[", "plout[", "gm_in[", "gm_out["],
    "mutation_strategy": "Use a signed integer variable, enum value, or expression that evaluates to a negative number as an endpoint array index (e.g., k[-1].in[0] or k[i-2].out[0] where i=0), or use a non-integer type like a float literal as an index.",
    "repair_expectation": "Replace the invalid index expression with a valid non-negative integer literal or constexpr that resolves to a valid array position.",
    "validation_signal": "WSL Vitis/AIE compile failure with invalid array subscript, type mismatch, or negative index error.",
    "tags": ["array_subscript", "endpoint_index", "graph_endpoint_indices", "negative_index", "type_error"]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for AIE graph files
    if 'graph' in lower:
        return True
    if lower.endswith('.h') or lower.endswith('.hpp') or lower.endswith('.cpp') or lower.endswith('.cc'):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match endpoint index expressions like .in[0], .out[1], k[0], plin[2], etc.
    # We look for the match_targets followed by a non-negative integer index and closing bracket
    # The pattern captures the prefix, the index content, and ensures it's a valid integer literal
    
    # Build a regex that matches any of the target patterns with a non-negative integer index
    # Escape the match targets for regex
    target_patterns = [
        r'\.in\[',
        r'\.out\[',
        r'k\[',
        r'plin\[',
        r'plout\[',
        r'gm_in\[',
        r'gm_out\['
    ]
    
    # Combined pattern: match target followed by a non-negative integer literal and ]
    combined_pattern = r'(' + '|'.join(target_patterns) + r')(\d+)(\])'
    
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        
        for match in re.finditer(combined_pattern, content):
            prefix = match.group(1)  # e.g., ".in["
            index_str = match.group(2)  # e.g., "0"
            suffix = match.group(3)  # "]"
            
            original_fragment = match.group(0)  # e.g., ".in[0]"
            start = match.start()
            end = match.end()
            
            # Determine mutation: replace the integer index with a negative expression or float
            index_val = int(index_str)
            
            # Strategy 1: Use negative index (-1)
            negative_replacement = prefix + "-1" + suffix
            
            candidates.append({
                "file_path": filepath,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_fragment,
                "replacement": negative_replacement,
                "description": f"Replace valid index '{index_str}' with negative index '-1' in endpoint expression '{original_fragment}'"
            })
            
            # Strategy 2: Use float literal as index
            float_replacement = prefix + "0.5" + suffix
            
            candidates.append({
                "file_path": filepath,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_fragment,
                "replacement": float_replacement,
                "description": f"Replace valid integer index '{index_str}' with float literal '0.5' in endpoint expression '{original_fragment}'"
            })
            
            # Strategy 3: Use expression that evaluates to negative (index - larger_number)
            if index_val >= 0:
                expr_replacement = prefix + f"{index_val} - 2" + suffix
                candidates.append({
                    "file_path": filepath,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": start,
                    "end": end,
                    "original": original_fragment,
                    "replacement": expr_replacement,
                    "description": f"Replace valid index '{index_str}' with expression '{index_val} - 2' (evaluates to {index_val - 2}) in endpoint expression '{original_fragment}'"
                })
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate["file_path"]
    content = project_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        idx = content.find(original)
        if idx == -1:
            # Cannot apply mutation, return unchanged
            return new_files
        new_content = content[:idx] + replacement + content[idx + len(original):]
    else:
        new_content = content[:start] + replacement + content[end:]
    
    new_files[filepath] = new_content
    return new_files
