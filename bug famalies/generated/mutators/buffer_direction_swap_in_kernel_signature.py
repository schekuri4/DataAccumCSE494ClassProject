import re
import copy

BUG_FAMILY = {
    "family_id": "BF134",
    "bug_type": "buffer_direction_swap_in_kernel_signature",
    "category": "buffer_interfaces",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["input_buffer<", "output_buffer<", "void kernel_func("],
    "mutation_strategy": "Swap input_buffer and output_buffer in the kernel function signature so that what should be an input is declared as output_buffer and vice versa. The graph connection remains unchanged, causing a direction mismatch between the kernel registration and the actual port connections.",
    "repair_expectation": "Restore the correct input_buffer/output_buffer declarations matching the graph's connect<> port directions.",
    "validation_signal": "WSL Vitis/AIE compile failure with port direction mismatch or incompatible kernel port type error.",
    "tags": ["buffer_interfaces", "direction_mismatch", "input_buffer", "kernel_port", "output_buffer"]
}


def _is_kernel_file(path, content):
    """Heuristic: file is a kernel source or header if it contains buffer declarations in function signatures."""
    if not (path.endswith('.h') or path.endswith('.hpp') or path.endswith('.cpp') or path.endswith('.cc')):
        return False
    if 'input_buffer<' in content or 'output_buffer<' in content:
        return True
    return False


def _find_function_signatures_with_buffers(content):
    """Find function signatures that contain both input_buffer and output_buffer parameters."""
    # Match function signatures (possibly spanning multiple lines)
    # We look for patterns like: void func_name(...) or similar return types
    # We'll find all top-level function declarations/definitions with buffer params
    
    # Pattern to find function signatures containing buffer parameters
    # This regex captures the full parameter list of functions
    func_pattern = re.compile(
        r'((?:void|int|float|double|auto|bool)\s+\w+\s*\()(.*?)(\))',
        re.DOTALL
    )
    
    results = []
    for match in func_pattern.finditer(content):
        params_text = match.group(2)
        if 'input_buffer<' in params_text or 'output_buffer<' in params_text:
            results.append({
                'full_match': match.group(0),
                'start': match.start(),
                'end': match.end(),
                'prefix': match.group(1),
                'params': params_text,
                'suffix': match.group(3),
                'params_start': match.start(2),
                'params_end': match.end(2)
            })
    return results


def find_mutation_candidates(project_files):
    candidates = []
    
    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path, content):
            continue
        
        signatures = _find_function_signatures_with_buffers(content)
        
        for sig in signatures:
            params_text = sig['params']
            
            # Find all input_buffer and output_buffer occurrences in this signature
            input_occurrences = list(re.finditer(r'input_buffer<', params_text))
            output_occurrences = list(re.finditer(r'output_buffer<', params_text))
            
            if not input_occurrences and not output_occurrences:
                continue
            
            # We need at least one of each to do a meaningful swap,
            # or at least one occurrence to swap direction
            # Strategy: swap all input_buffer <-> output_buffer in the params
            
            if not input_occurrences and not output_occurrences:
                continue
            
            # Only mutate if there's at least one buffer keyword to swap
            if not (input_occurrences or output_occurrences):
                continue
            
            # Create the swapped version of params
            # Use a placeholder to avoid double-replacement
            placeholder = "___PLACEHOLDER_BUFFER___<"
            swapped_params = params_text.replace('input_buffer<', placeholder)
            swapped_params = swapped_params.replace('output_buffer<', 'input_buffer<')
            swapped_params = swapped_params.replace(placeholder, 'output_buffer<')
            
            if swapped_params == params_text:
                continue  # No actual change
            
            original_full = sig['full_match']
            replacement_full = sig['prefix'] + swapped_params + sig['suffix']
            
            description = (
                f"Swap input_buffer and output_buffer directions in kernel function signature "
                f"in {file_path} at position {sig['start']}-{sig['end']}. "
                f"This causes a port direction mismatch between kernel registration and graph connections."
            )
            
            candidates.append({
                'file_path': file_path,
                'bug_type': BUG_FAMILY['bug_type'],
                'category': BUG_FAMILY['category'],
                'start': sig['start'],
                'end': sig['end'],
                'original': original_full,
                'replacement': replacement_full,
                'description': description
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate['file_path']
    original = candidate['original']
    replacement = candidate['replacement']
    
    if file_path not in new_files:
        return new_files
    
    content = new_files[file_path]
    
    # Replace at the exact position
    start = candidate['start']
    end = candidate['end']
    
    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
