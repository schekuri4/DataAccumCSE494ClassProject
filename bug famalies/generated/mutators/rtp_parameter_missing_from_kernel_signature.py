import re
import copy

BUG_FAMILY = {
    "family_id": "BF039",
    "bug_type": "rtp_parameter_missing_from_kernel_signature",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel header", "kernel source", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<parameter>(",
        "adf::connect<adf::parameter>",
        "async(",
        "int32",
        "runtime<ratio>"
    ],
    "mutation_strategy": "Remove the RTP scalar parameter (e.g., int32 coefficient) from the kernel function prototype while keeping the graph's connect<parameter> binding to that port index, causing a port count or type mismatch.",
    "repair_expectation": "Re-add the RTP parameter to the kernel function signature at the correct position matching the graph's port binding.",
    "validation_signal": "WSL Vitis/AIE compile failure with port index out of range or parameter port binding error.",
    "tags": [
        "kernel_prototypes_and_signatures",
        "missing_param",
        "port_binding",
        "rtp",
        "runtime_parameter"
    ]
}


def _is_kernel_file(path):
    """Heuristic: kernel headers/sources are .h or .cc/.cpp files not named *graph*."""
    lower = path.lower()
    return (lower.endswith('.h') or lower.endswith('.hpp') or
            lower.endswith('.cc') or lower.endswith('.cpp') or
            lower.endswith('.c'))


def _is_graph_file(path):
    lower = path.lower()
    return 'graph' in lower and (lower.endswith('.h') or lower.endswith('.hpp') or lower.endswith('.cpp'))


def _has_rtp_binding(project_files):
    """Check if any file has connect<parameter> or async( usage indicating RTP."""
    for content in project_files.values():
        if 'connect<parameter>' in content or 'connect<adf::parameter>' in content:
            return True
        if 'async(' in content:
            return True
    return False


def _find_rtp_params_in_function(content, file_path):
    """Find function declarations/definitions with RTP-like scalar parameters (int32, int, float, etc.)."""
    candidates = []
    # Match function signatures: return_type func_name(params)
    # We look for parameters that are scalar types commonly used as RTP
    rtp_scalar_types = r'(?:int32|int32_t|uint32|uint32_t|int16|int16_t|uint16_t|int8|int8_t|uint8_t|int|float|double|uint32|int64_t|uint64_t)'
    
    # Pattern to find function declarations/definitions
    func_pattern = re.compile(
        r'((?:void|int|float|double|bool|auto)\s+\w+\s*\()'  # return type + name + open paren
        r'([^)]*)'  # parameters
        r'(\)\s*[;{])',  # close paren + semicolon or brace
        re.MULTILINE
    )
    
    for match in func_pattern.finditer(content):
        params_str = match.group(2)
        # Split parameters by comma
        params = _split_params(params_str)
        if len(params) < 2:
            continue
        
        # Find RTP-like scalar parameters (not input_window, output_window, input_buffer, etc.)
        stream_types = ['window', 'buffer', 'stream', 'input_window', 'output_window',
                        'input_buffer', 'output_buffer', 'input_stream', 'output_stream',
                        'input_pktstream', 'output_pktstream']
        
        for i, param in enumerate(params):
            param_stripped = param.strip()
            # Skip if it's a stream/window/buffer type
            is_stream = any(st in param_stripped.lower() for st in stream_types)
            if is_stream:
                continue
            # Check if it matches an RTP scalar type
            if re.search(rtp_scalar_types, param_stripped):
                # This is a candidate RTP parameter to remove
                full_match_start = match.start()
                full_match_end = match.end()
                original_text = match.group(0)
                
                # Build replacement with this parameter removed
                new_params = [p for j, p in enumerate(params) if j != i]
                new_params_str = ', '.join(p.strip() for p in new_params)
                replacement_text = match.group(1) + new_params_str + match.group(3)
                
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_parameter_missing_from_kernel_signature",
                    "category": "kernel_prototypes_and_signatures",
                    "start": full_match_start,
                    "end": full_match_end,
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": f"Remove RTP scalar parameter '{param_stripped}' (index {i}) from kernel function signature to cause port count/type mismatch with graph's connect<parameter> binding."
                })
    
    return candidates


def _split_params(params_str):
    """Split parameter string by commas, respecting template angle brackets."""
    params = []
    depth = 0
    current = []
    for ch in params_str:
        if ch == '<':
            depth += 1
            current.append(ch)
        elif ch == '>':
            depth -= 1
            current.append(ch)
        elif ch == '(' :
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            params.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        params.append(''.join(current))
    return params


def find_mutation_candidates(project_files):
    """Find all candidate RTP parameters that can be removed from kernel function signatures."""
    candidates = []
    
    # First check if there's evidence of RTP usage in the project
    has_rtp = _has_rtp_binding(project_files)
    
    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue
        
        # For kernel files (not graph files), look for function signatures with RTP params
        # If there's no RTP binding evidence, we still look but with stricter criteria
        file_candidates = _find_rtp_params_in_function(content, file_path)
        
        if has_rtp:
            candidates.extend(file_candidates)
        else:
            # Even without explicit RTP binding, if the file has int32 params in
            # what looks like a kernel function, include it
            for c in file_candidates:
                if 'int32' in c['original'] or 'coefficient' in c['original'].lower() or 'rtp' in c['original'].lower():
                    candidates.append(c)
    
    # If no candidates found in non-graph kernel files, also check graph headers
    # for inline kernel declarations
    if not candidates:
        for file_path, content in project_files.items():
            if _is_graph_file(file_path):
                file_candidates = _find_rtp_params_in_function(content, file_path)
                candidates.extend(file_candidates)
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply the mutation to remove the RTP parameter from the kernel signature."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    if file_path not in new_files:
        return new_files
    
    content = new_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    
    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
