import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF154",
    "bug_type": "window_dimensions_mismatch",
    "category": "graph_runtime_constraints",
    "target_files": ["graph header", "kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<window<",
        "input_window",
        "output_window",
        "dimensions",
        "window_size"
    ],
    "mutation_strategy": "Change the window size template parameter in the graph connect statement to a value that does not match the kernel function signature's expected window size (e.g., connect<window<256>> when kernel expects window<512>), or use a non-multiple-of-vector-lane-width dimension, causing a compile/link-time dimension mismatch.",
    "repair_expectation": "Align the window size in the graph connect<window<N>> with the kernel's declared input_window<type> size parameter so both agree on byte count.",
    "validation_signal": "WSL Vitis/AIE compile failure with window size mismatch error or constraint violation between graph and kernel interface.",
    "tags": [
        "compile_time",
        "connect",
        "dimensions",
        "graph_runtime_constraints",
        "kernel_interface",
        "window_size"
    ]
}


def _is_graph_file(path: str) -> bool:
    """Heuristic: graph headers are .h/.hpp files with 'graph' in name or content with connect<window<"""
    lower = path.lower()
    return lower.endswith(('.h', '.hpp', '.cpp', '.cc')) 


def _mutate_window_size(original_size: str) -> str:
    """Return a mismatched window size value."""
    try:
        val = int(original_size.strip())
    except ValueError:
        # If it's an expression, just multiply by 2
        return f"({original_size.strip()}) * 2"
    
    # Pick a value that's clearly different and likely not a multiple of vector lane width
    if val > 128:
        return str(val // 2)
    else:
        return str(val * 3)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    
    # Pattern 1: connect<window<N>> in graph files
    # Matches patterns like: connect<window<256>> or connect< window< 512 > >
    window_connect_pattern = re.compile(
        r'(connect\s*<\s*window\s*<\s*)(\d+)(\s*>\s*>)'
    )
    
    # Pattern 2: window_size(...) or window_size = N
    window_size_pattern = re.compile(
        r'(window_size\s*[\(=]\s*)(\d+)(\s*[\);])'
    )
    
    # Pattern 3: dimensions(...) with numeric arg
    dimensions_pattern = re.compile(
        r'(dimensions\s*\(\s*)(\d+)(\s*\))'
    )
    
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        
        # Check if file likely contains graph or kernel window constructs
        has_window_connect = 'connect' in content and 'window' in content
        has_window_size = 'window_size' in content
        has_dimensions = 'dimensions' in content
        has_input_window = 'input_window' in content or 'output_window' in content
        
        if not (has_window_connect or has_window_size or has_dimensions or has_input_window):
            continue
        
        # Pattern 1: connect<window<N>>
        for match in window_connect_pattern.finditer(content):
            original_size = match.group(2)
            new_size = _mutate_window_size(original_size)
            original_text = match.group(0)
            replacement_text = match.group(1) + new_size + match.group(3)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_dimensions_mismatch",
                "category": "graph_runtime_constraints",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed window size in connect<window<{original_size}>> to "
                    f"connect<window<{new_size}>> causing a mismatch with the kernel's "
                    f"expected window size."
                )
            })
        
        # Pattern 2: window_size(N) or window_size = N
        for match in window_size_pattern.finditer(content):
            original_size = match.group(2)
            new_size = _mutate_window_size(original_size)
            original_text = match.group(0)
            replacement_text = match.group(1) + new_size + match.group(3)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_dimensions_mismatch",
                "category": "graph_runtime_constraints",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed window_size from {original_size} to {new_size} "
                    f"causing a dimension mismatch between graph and kernel interface."
                )
            })
        
        # Pattern 3: dimensions(N)
        for match in dimensions_pattern.finditer(content):
            original_size = match.group(2)
            new_size = _mutate_window_size(original_size)
            original_text = match.group(0)
            replacement_text = match.group(1) + new_size + match.group(3)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_dimensions_mismatch",
                "category": "graph_runtime_constraints",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed dimensions from {original_size} to {new_size} "
                    f"causing a window dimension mismatch."
                )
            })
    
    # Also look for input_window/output_window template size parameters in kernel sources
    # Pattern: input_window<cint16, 256> or input_window_size = 256
    kernel_window_pattern = re.compile(
        r'((input_window|output_window)\s*<\s*\w+\s*,\s*)(\d+)(\s*>)'
    )
    
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        if 'input_window' not in content and 'output_window' not in content:
            continue
        
        for match in kernel_window_pattern.finditer(content):
            original_size = match.group(3)
            new_size = _mutate_window_size(original_size)
            original_text = match.group(0)
            replacement_text = match.group(1) + new_size + match.group(4)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_dimensions_mismatch",
                "category": "graph_runtime_constraints",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed {match.group(2)} size from {original_size} to {new_size} "
                    f"causing a mismatch with the graph connect window size."
                )
            })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    original_content = new_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if original_content[start:end] == original:
        new_content = original_content[:start] + replacement + original_content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = original_content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
