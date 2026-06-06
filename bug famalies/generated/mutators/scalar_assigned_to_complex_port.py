import re
import copy

BUG_FAMILY = {
    "family_id": "BF271",
    "bug_type": "scalar_assigned_to_complex_port",
    "category": "complex_datatypes",
    "target_files": ["graph header", "kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_plio",
        "output_plio",
        "connect<stream,stream>",
        "cint16",
        "int16",
        "adf::connect"
    ],
    "mutation_strategy": "In the graph header, change a connect<> template parameter or PLIO type from cint16 to int16 (or int32 instead of cint32), creating a type mismatch between the kernel port declaration (which expects complex) and the graph connection (which provides scalar). Alternatively, change the kernel port signature from cint16 to int16 while leaving the graph connect template unchanged.",
    "repair_expectation": "Restore consistent complex type (e.g., cint16) in both the kernel port declaration and the graph connect<> template instantiation.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch or template instantiation error on the connect<> or port binding.",
    "tags": [
        "cint16",
        "complex_datatypes",
        "connect",
        "int16",
        "plio",
        "scalar_vs_complex",
        "type_mismatch"
    ]
}

# Map complex types to their scalar counterparts
COMPLEX_TO_SCALAR = {
    "cint16": "int16",
    "cint32": "int32",
    "cfloat": "float",
}


def _is_graph_header(path):
    """Heuristic: graph headers are .h/.hpp files with 'graph' in name or content patterns."""
    return path.endswith(('.h', '.hpp'))


def _is_kernel_source(path):
    """Heuristic: kernel sources are .cpp/.cc/.h/.hpp files."""
    return path.endswith(('.cpp', '.cc', '.h', '.hpp'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        # Strategy 1: In graph headers, find cint16/cint32 in connect<> templates or PLIO declarations
        if _is_graph_header(file_path):
            # Look for connect<...> with complex types
            # Pattern: connect< ... cint16 ... > or adf::connect< ... cint16 ... >
            connect_pattern = re.compile(
                r'((?:adf::)?connect\s*<[^>]*?)\b(cint16|cint32|cfloat)\b([^>]*?>)'
            )
            for m in connect_pattern.finditer(content):
                complex_type = m.group(2)
                scalar_type = COMPLEX_TO_SCALAR[complex_type]
                start = m.start(2)
                end = m.end(2)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "scalar_assigned_to_complex_port",
                    "category": "complex_datatypes",
                    "start": start,
                    "end": end,
                    "original": complex_type,
                    "replacement": scalar_type,
                    "description": f"Change {complex_type} to {scalar_type} in connect<> template, creating type mismatch with kernel port expecting complex type."
                })

            # Look for PLIO declarations with complex types
            # Pattern: input_plio::create(...cint16...) or plio type specifications
            plio_pattern = re.compile(
                r'((?:input_plio|output_plio)\s*(?:::create\s*\(|[^;]*?))\b(cint16|cint32|cfloat)\b'
            )
            for m in plio_pattern.finditer(content):
                complex_type = m.group(2)
                scalar_type = COMPLEX_TO_SCALAR[complex_type]
                start = m.start(2)
                end = m.end(2)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "scalar_assigned_to_complex_port",
                    "category": "complex_datatypes",
                    "start": start,
                    "end": end,
                    "original": complex_type,
                    "replacement": scalar_type,
                    "description": f"Change {complex_type} to {scalar_type} in PLIO declaration, creating type mismatch with kernel port expecting complex type."
                })

            # Also look for standalone complex type usage in port/stream declarations in graph
            port_pattern = re.compile(
                r'(port\s*<\s*(?:input|output)\s*>\s*[^;]*?)\b(cint16|cint32|cfloat)\b'
            )
            for m in port_pattern.finditer(content):
                complex_type = m.group(2)
                scalar_type = COMPLEX_TO_SCALAR[complex_type]
                start = m.start(2)
                end = m.end(2)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "scalar_assigned_to_complex_port",
                    "category": "complex_datatypes",
                    "start": start,
                    "end": end,
                    "original": complex_type,
                    "replacement": scalar_type,
                    "description": f"Change {complex_type} to {scalar_type} in port declaration in graph header, creating type mismatch."
                })

        # Strategy 2: In kernel source, change kernel port signature from complex to scalar
        if _is_kernel_source(file_path):
            # Look for function parameters or port declarations with complex types
            # Pattern: cint16 in function signatures or input_window/input_stream declarations
            kernel_sig_pattern = re.compile(
                r'((?:input_window|output_window|input_stream|output_stream)\s*<\s*)(cint16|cint32|cfloat)(\s*>)'
            )
            for m in kernel_sig_pattern.finditer(content):
                complex_type = m.group(2)
                scalar_type = COMPLEX_TO_SCALAR[complex_type]
                start = m.start(2)
                end = m.end(2)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "scalar_assigned_to_complex_port",
                    "category": "complex_datatypes",
                    "start": start,
                    "end": end,
                    "original": complex_type,
                    "replacement": scalar_type,
                    "description": f"Change kernel port type from {complex_type} to {scalar_type} in kernel source, creating mismatch with graph connect<> template."
                })

            # Also look for input_buffer/output_buffer patterns (newer AIE API)
            buffer_pattern = re.compile(
                r'((?:input_buffer|output_buffer)\s*<\s*)(cint16|cint32|cfloat)(\s*[,>])'
            )
            for m in buffer_pattern.finditer(content):
                complex_type = m.group(2)
                scalar_type = COMPLEX_TO_SCALAR[complex_type]
                start = m.start(2)
                end = m.end(2)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "scalar_assigned_to_complex_port",
                    "category": "complex_datatypes",
                    "start": start,
                    "end": end,
                    "original": complex_type,
                    "replacement": scalar_type,
                    "description": f"Change kernel buffer type from {complex_type} to {scalar_type} in kernel source, creating mismatch with graph connect<> template."
                })

    # Deduplicate candidates by (file_path, start, end)
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c["file_path"], c["start"], c["end"], c["original"], c["replacement"])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    return unique_candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
