import re
import copy

BUG_FAMILY = {
    "family_id": "BF152",
    "bug_type": "repetition_count_non_positive",
    "category": "graph_runtime_constraints",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "repetition_count",
        "adf::repetition_count",
        "graph.init()",
        "graph.run()"
    ],
    "mutation_strategy": "Set repetition_count to zero or a negative integer in the graph constraint specification, or pass a non-integer type (e.g., repetition_count(0) or repetition_count(-1)), which violates compile-time or link-time constraints.",
    "repair_expectation": "Change the repetition_count argument to a positive integer >= 1, e.g., repetition_count(1) or remove the constraint to use default infinite run.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about invalid repetition count value or type mismatch in constraint.",
    "tags": [
        "compile_time",
        "graph_constraint",
        "graph_runtime_constraints",
        "invalid_literal",
        "repetition_count"
    ]
}


def _is_graph_file(filepath):
    """Heuristic: graph headers (.h/.hpp) or graph sources (.cpp/.cc) containing graph-related content."""
    lower = filepath.lower()
    return any(ext in lower for ext in ['.h', '.hpp', '.cpp', '.cc', '.c'])


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern 1: repetition_count(N) where N is a positive integer - mutate to 0 or negative
    pattern_rep_count = re.compile(
        r'((?:adf::)?repetition_count)\s*\(\s*([^)]+)\s*\)'
    )
    
    # Pattern 2: graph.run(N) where N is a positive integer - mutate to 0 or negative
    pattern_graph_run = re.compile(
        r'(\w+\.run)\s*\(\s*(\d+)\s*\)'
    )
    
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        
        # Check if file has any graph-related content
        has_graph_content = any(
            kw in content for kw in ['repetition_count', 'graph', 'adf::', 'kernel', 'Graph']
        )
        if not has_graph_content:
            continue
        
        # Find repetition_count(...) calls
        for match in pattern_rep_count.finditer(content):
            func_name = match.group(1)
            arg = match.group(2).strip()
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            
            # Try to parse the argument as a positive integer
            try:
                val = int(arg)
                if val > 0:
                    # Mutate to 0
                    replacement_zero = f"{func_name}(0)"
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "repetition_count_non_positive",
                        "category": "graph_runtime_constraints",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement_zero,
                        "description": f"Set repetition_count to 0 (was {val}), violating the positive integer requirement."
                    })
                    # Mutate to -1
                    replacement_neg = f"{func_name}(-1)"
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "repetition_count_non_positive",
                        "category": "graph_runtime_constraints",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement_neg,
                        "description": f"Set repetition_count to -1 (was {val}), violating the positive integer requirement."
                    })
                elif val <= 0:
                    # Already non-positive, skip
                    pass
            except ValueError:
                # Argument is not a simple integer literal (could be a variable or expression)
                # Mutate to 0
                replacement_zero = f"{func_name}(0)"
                candidates.append({
                    "file_path": filepath,
                    "bug_type": "repetition_count_non_positive",
                    "category": "graph_runtime_constraints",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement_zero,
                    "description": f"Set repetition_count to 0 (was '{arg}'), violating the positive integer requirement."
                })
        
        # Find graph.run(N) calls with positive integer
        for match in pattern_graph_run.finditer(content):
            run_call = match.group(1)
            arg = match.group(2).strip()
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            
            try:
                val = int(arg)
                if val > 0:
                    # Mutate to 0
                    replacement_zero = f"{run_call}(0)"
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "repetition_count_non_positive",
                        "category": "graph_runtime_constraints",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement_zero,
                        "description": f"Set graph.run() iteration count to 0 (was {val}), which is non-positive."
                    })
                    # Mutate to -1
                    replacement_neg = f"{run_call}(-1)"
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "repetition_count_non_positive",
                        "category": "graph_runtime_constraints",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement_neg,
                        "description": f"Set graph.run() iteration count to -1 (was {val}), which is non-positive."
                    })
            except ValueError:
                pass
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate["file_path"]
    content = new_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)
    
    new_files[filepath] = new_content
    return new_files
