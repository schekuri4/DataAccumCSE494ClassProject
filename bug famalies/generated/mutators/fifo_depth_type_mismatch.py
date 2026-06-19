import re
import copy

BUG_FAMILY = {
    "family_id": "BF153",
    "bug_type": "fifo_depth_type_mismatch",
    "category": "graph_runtime_constraints",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "fifo_depth",
        "adf::fifo_depth",
        "connect<stream",
        "connect<window"
    ],
    "mutation_strategy": "Replace the FIFO depth integer argument with a floating-point literal, a string, or a template type parameter that does not resolve to an integer (e.g., fifo_depth(32.5) or fifo_depth(\"128\")), causing a compile-time type error in the ADF constraint API.",
    "repair_expectation": "Replace the FIFO depth argument with a valid positive integer literal or constexpr integer, e.g., fifo_depth(128).",
    "validation_signal": "WSL Vitis/AIE compile failure with template argument deduction failure or type mismatch in fifo_depth constraint.",
    "tags": [
        "compile_time",
        "fifo_depth",
        "graph_constraint",
        "graph_runtime_constraints",
        "type_mismatch"
    ]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or graph source files."""
    lower = filepath.lower()
    # Common patterns for graph files in AIE projects
    if any(ext in lower for ext in ['.h', '.hpp', '.cpp', '.cc']):
        if 'graph' in lower:
            return True
        return True  # Be permissive; we'll check content for fifo_depth usage
    return False


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match fifo_depth calls with integer arguments
    # Matches: fifo_depth(123), adf::fifo_depth(123), fifo_depth( 64 ), etc.
    fifo_depth_pattern = re.compile(
        r'((?:adf::)?fifo_depth)\s*\(\s*(\d+)\s*\)'
    )
    fifo_depth_assignment_pattern = re.compile(
        r'((?:adf::)?fifo_depth\s*\(\s*[^)]*?\s*\)\s*=\s*)(\d+)(\s*;)'
    )
    
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        
        # Check if file contains any of the match targets
        has_match_target = any(target in content for target in BUG_FAMILY["match_targets"])
        if not has_match_target:
            continue
        
        for match in fifo_depth_pattern.finditer(content):
            func_name = match.group(1)
            int_value = match.group(2)
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            
            # Generate multiple mutation variants
            mutations = [
                # Float literal mutation
                (f'{func_name}({int_value}.5)',
                 f"Replace integer FIFO depth {int_value} with float literal {int_value}.5"),
                # String literal mutation
                (f'{func_name}("{int_value}")',
                 f'Replace integer FIFO depth {int_value} with string literal "{int_value}"'),
                # Another float variant
                (f'{func_name}(0.0)',
                 f"Replace integer FIFO depth {int_value} with float literal 0.0"),
            ]
            
            for replacement, description in mutations:
                candidates.append({
                    "file_path": filepath,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": description
                })

        for match in fifo_depth_assignment_pattern.finditer(content):
            int_value = match.group(2)
            original = match.group(0)
            for replacement_value, description in [
                (f"{int_value}.5", f"Replace FIFO depth assignment {int_value} with float literal."),
                (f"\"{int_value}\"", f"Replace FIFO depth assignment {int_value} with string literal."),
            ]:
                candidates.append({
                    "file_path": filepath,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": match.group(1) + replacement_value + match.group(3),
                    "description": description
                })
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate["file_path"]
    original_content = new_project_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original_text = candidate["original"]
    replacement_text = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if original_content[start:end] == original_text:
        new_content = original_content[:start] + replacement_text + original_content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = original_content.replace(original_text, replacement_text, 1)
    
    new_project_files[filepath] = new_content
    return new_project_files
