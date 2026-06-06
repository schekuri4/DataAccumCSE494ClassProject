import re
import copy

BUG_FAMILY = {
    "family_id": "BF142",
    "bug_type": "input_cascade_port_declared_as_input_stream",
    "category": "cascade_streams",
    "target_files": ["kernel header", "kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_cascade",
        "input_stream",
        "input_stream<acc48>",
        "input_cascade_acc48"
    ],
    "mutation_strategy": "Change a kernel function signature parameter from input_cascade<acc48>* to input_stream<acc48>* or input_stream_acc48*, causing a type mismatch when the graph connects a cascade port to this kernel.",
    "repair_expectation": "Restore the parameter type to input_cascade<acc48>* (or the appropriate accumulator width) matching the graph port declaration.",
    "validation_signal": "WSL Vitis/AIE compile failure with port type incompatibility or unresolved kernel signature error from aiecompiler.",
    "tags": [
        "cascade_streams",
        "input_cascade",
        "input_stream",
        "kernel_signature",
        "port_type"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find all occurrences of input_cascade<accXX>* in kernel headers/sources."""
    candidates = []
    
    # Pattern matches input_cascade<acc48>, input_cascade<acc80>, etc. with optional pointer/ref
    # We look for the full type expression including template parameter
    pattern = re.compile(
        r'input_cascade\s*<\s*(acc\d+)\s*>\s*\*'
    )
    
    # Also match input_cascade_acc48 style (underscore variant)
    pattern_underscore = re.compile(
        r'input_cascade_(acc\d+)\s*\*'
    )
    
    # Target file extensions for kernel headers and sources
    target_extensions = ('.h', '.hpp', '.hh', '.cc', '.cpp', '.c')
    
    for file_path, content in project_files.items():
        # Filter to likely kernel header/source files
        if not any(file_path.endswith(ext) for ext in target_extensions):
            continue
        
        # Search for input_cascade<accXX>* pattern
        for match in pattern.finditer(content):
            acc_type = match.group(1)  # e.g., "acc48"
            original = match.group(0)
            # Replace input_cascade with input_stream
            replacement = re.sub(r'input_cascade', 'input_stream', original)
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "input_cascade_port_declared_as_input_stream",
                "category": "cascade_streams",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed 'input_cascade<{acc_type}>*' to 'input_stream<{acc_type}>*' "
                    f"in {file_path}, causing cascade port type mismatch."
                )
            })
        
        # Search for input_cascade_accXX* pattern (underscore variant)
        for match in pattern_underscore.finditer(content):
            acc_type = match.group(1)  # e.g., "acc48"
            original = match.group(0)
            replacement = original.replace('input_cascade_', 'input_stream_')
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "input_cascade_port_declared_as_input_stream",
                "category": "cascade_streams",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed 'input_cascade_{acc_type}*' to 'input_stream_{acc_type}*' "
                    f"in {file_path}, causing cascade port type mismatch."
                )
            })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_project_files[file_path]
    
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
    
    new_project_files[file_path] = new_content
    return new_project_files
