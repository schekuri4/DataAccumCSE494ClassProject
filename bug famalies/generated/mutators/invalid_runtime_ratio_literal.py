import re
import copy

BUG_FAMILY = {
    "family_id": "BF151",
    "bug_type": "invalid_runtime_ratio_literal",
    "category": "graph_runtime_constraints",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["runtime<ratio>", "adf::runtime<ratio>", "kernel::create"],
    "mutation_strategy": "Replace the runtime<ratio> parameter with an invalid literal such as a negative number, zero, a string, or a value exceeding 1.0 (e.g., runtime<ratio>(1.5) or runtime<ratio>(-0.3)), causing a compile-time or elaboration-time constraint violation.",
    "repair_expectation": "Replace the invalid ratio literal with a valid floating-point value in the range (0.0, 1.0], e.g., runtime<ratio>(0.9).",
    "validation_signal": "WSL Vitis/AIE compile failure with error referencing invalid runtime ratio constraint or static_assert on ratio bounds.",
    "tags": ["compile_time", "graph_constraint", "graph_runtime_constraints", "invalid_literal", "runtime_ratio"]
}

# Pattern to match runtime<ratio>(value) with optional adf:: namespace prefix
_RUNTIME_RATIO_PATTERN = re.compile(
    r'((?:adf::)?runtime\s*<\s*ratio\s*>\s*\(\s*)'
    r'([^)]+?)'
    r'(\s*\))'
)

# Invalid replacement values to use
_INVALID_VALUES = ["1.5", "-0.3", "0.0", "2.0", "-1.0"]


def _is_graph_file(file_path):
    """Heuristic to determine if a file is a graph header or graph source."""
    lower = file_path.lower()
    # Check for common graph file patterns
    if 'graph' in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might be graph files
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        
        # Look for runtime<ratio>(...) patterns
        for match in _RUNTIME_RATIO_PATTERN.finditer(content):
            original_value = match.group(2).strip()
            full_original = match.group(0)
            start = match.start()
            end = match.end()
            
            # Try to parse the original value to confirm it's a valid number
            try:
                val = float(original_value)
                # Only mutate if the current value is valid (0 < val <= 1.0)
                if val <= 0.0 or val > 1.0:
                    continue  # Already invalid, skip
            except ValueError:
                continue  # Not a simple numeric literal, skip
            
            # Choose an invalid replacement that differs from original
            replacement_value = None
            for inv in _INVALID_VALUES:
                try:
                    if float(inv) != val:
                        replacement_value = inv
                        break
                except ValueError:
                    replacement_value = inv
                    break
            
            if replacement_value is None:
                replacement_value = "1.5"
            
            replacement_text = match.group(1) + replacement_value + match.group(3)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "invalid_runtime_ratio_literal",
                "category": "graph_runtime_constraints",
                "start": start,
                "end": end,
                "original": full_original,
                "replacement": replacement_text,
                "description": (
                    f"Replace valid runtime<ratio> value '{original_value}' with "
                    f"invalid value '{replacement_value}' to cause a constraint violation."
                )
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # Shallow copy of the dict
    
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
