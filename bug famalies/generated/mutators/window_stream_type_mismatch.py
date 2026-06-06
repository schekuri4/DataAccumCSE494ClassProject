import re
import copy

BUG_FAMILY = {
    "family_id": "BF033",
    "bug_type": "window_stream_type_mismatch",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel header", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_window<",
        "input_stream<",
        "output_window<",
        "output_stream<",
        "connect<window<",
        "connect<stream"
    ],
    "mutation_strategy": "Change a kernel parameter from input_window<int32>* to input_stream<int32>* (or vice versa) in the kernel header without updating the corresponding connect<window<...>> or connect<stream> in the graph, creating a window/stream interface mismatch.",
    "repair_expectation": "Make the kernel parameter type consistent with the graph connect<> template type (both window or both stream).",
    "validation_signal": "WSL Vitis/AIE compile failure with port type incompatibility error between kernel signature and graph connection.",
    "tags": [
        "connect_mismatch",
        "interface_type",
        "kernel_prototypes_and_signatures",
        "window_vs_stream"
    ]
}


# Pattern to match input_window<T> or input_stream<T> or output_window<T> or output_stream<T>
_PARAM_PATTERN = re.compile(
    r'\b(input|output)_(window|stream)\s*<\s*([^>]+)\s*>\s*\*'
)


def _is_header_file(path):
    """Check if a file looks like a header file."""
    return path.endswith('.h') or path.endswith('.hpp')


def _is_kernel_header(path, content):
    """Heuristic: kernel header contains window/stream parameter declarations."""
    if not _is_header_file(path):
        return False
    return bool(_PARAM_PATTERN.search(content))


def _is_graph_header(path, content):
    """Heuristic: graph header contains connect< statements."""
    if not _is_header_file(path):
        return False
    return 'connect<' in content


def _find_graph_file(project_files):
    """Find the graph header file."""
    for path, content in project_files.items():
        if _is_graph_header(path, content):
            return path
    return None


def find_mutation_candidates(project_files):
    candidates = []
    
    # Find graph file to check for connect<window< or connect<stream usage
    graph_file = _find_graph_file(project_files)
    graph_content = project_files.get(graph_file, "") if graph_file else ""
    
    # Determine what connection types exist in the graph
    has_window_connect = 'connect<window<' in graph_content
    has_stream_connect = 'connect<stream' in graph_content
    
    for path, content in project_files.items():
        if not _is_kernel_header(path, content):
            continue
        
        # Find all window/stream parameters in kernel headers
        for match in _PARAM_PATTERN.finditer(content):
            direction = match.group(1)  # input or output
            interface_type = match.group(2)  # window or stream
            data_type = match.group(3).strip()  # e.g., int32, cint16
            
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            
            # Determine the swap: window <-> stream
            if interface_type == "window":
                new_interface = "stream"
                # Only mutate if graph uses window connections (creating mismatch)
                if graph_file and not has_window_connect and not has_stream_connect:
                    # No graph connections found, still create candidate
                    pass
            else:
                new_interface = "window"
            
            replacement = f"{direction}_{new_interface}<{data_type}>*"
            
            description = (
                f"Change kernel parameter from {direction}_{interface_type}<{data_type}>* "
                f"to {direction}_{new_interface}<{data_type}>* without updating graph "
                f"connect<> type, creating window/stream interface mismatch."
            )
            
            candidates.append({
                "file_path": path,
                "bug_type": "window_stream_type_mismatch",
                "category": "kernel_prototypes_and_signatures",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
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
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    
    new_files[file_path] = new_content
    return new_files
