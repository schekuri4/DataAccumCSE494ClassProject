import re
import copy

BUG_FAMILY = {
    "family_id": "BF027",
    "bug_type": "connect_template_type_mismatch",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["connect<window<", "connect<stream", "connect<"],
    "mutation_strategy": "Change the connect<> template parameter to a mismatched type—e.g., use connect<stream> when the kernel expects window-based ports, use connect<window<256>> when the kernel port size is 512, or use connect<parameter> instead of connect<window<>>.",
    "repair_expectation": "Match the connect<> template parameter to the kernel's actual port type and size as declared in the prototype.",
    "validation_signal": "WSL Vitis/AIE compile failure with connection type incompatibility or window size mismatch error.",
    "tags": ["connect", "graph_kernel_binding", "port_mismatch", "template_type", "window_stream"]
}


def _is_graph_header(filepath):
    """Heuristic: graph headers are .h or .hpp files with 'graph' in name or content."""
    lower = filepath.lower()
    if lower.endswith(('.h', '.hpp', '.hh')):
        return True
    return False


def _generate_replacement(original_connect_type):
    """Generate a mismatched connect type based on the original."""
    stripped = original_connect_type.strip()
    
    # If it's a window type, try changing size or switching to stream
    window_match = re.match(r'window<\s*(\d+)\s*>', stripped)
    if window_match:
        size = int(window_match.group(1))
        # Option 1: change to stream
        return "stream"
    
    # If it's a stream type, switch to window
    if stripped.startswith('stream'):
        return "window<256>"
    
    # If it's a pktstream type, switch to window
    if stripped.startswith('pktstream'):
        return "window<256>"
    
    # If it's parameter, switch to window
    if stripped.startswith('parameter'):
        return "window<128>"
    
    # Default: switch to stream
    return "stream"


def _generate_window_size_replacement(original_connect_type):
    """For window types, generate a different window size."""
    window_match = re.match(r'window<\s*(\d+)\s*>', original_connect_type.strip())
    if window_match:
        size = int(window_match.group(1))
        # Double or halve the size
        new_size = size * 2 if size < 1024 else size // 2
        return f"window<{new_size}>"
    return None


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match connect< ... >( or connect< ... > (
    # We need to capture the template argument inside connect< >
    # connect<window<256>>, connect<stream>, connect<parameter>, etc.
    # The template can be nested (window<256>), so we handle angle bracket nesting
    connect_pattern = re.compile(
        r'connect\s*<\s*'
    )
    
    for filepath, content in project_files.items():
        if not _is_graph_header(filepath):
            # Also check if file contains graph-related content
            if 'connect<' not in content:
                continue
        
        if 'connect<' not in content:
            continue
        
        # Find all connect< occurrences and extract the template parameter
        pos = 0
        while pos < len(content):
            match = connect_pattern.search(content, pos)
            if not match:
                break
            
            # Find the matching closing > for the template parameter
            start_of_connect = match.start()
            template_start = match.end()  # position right after 'connect<' (and whitespace)
            
            # Parse nested angle brackets to find the closing >
            depth = 1
            i = template_start
            while i < len(content) and depth > 0:
                if content[i] == '<':
                    depth += 1
                elif content[i] == '>':
                    depth -= 1
                i += 1
            
            if depth != 0:
                pos = template_start
                continue
            
            # template_end is position of the closing >
            template_end = i - 1
            template_content = content[template_start:template_end]
            
            # The full connect<...> span
            full_original = content[start_of_connect:i]
            
            # Generate type mismatch replacement
            replacement_type = _generate_replacement(template_content)
            replacement_full = f"connect<{replacement_type}>"
            
            if replacement_full != full_original:
                candidates.append({
                    "file_path": filepath,
                    "bug_type": "connect_template_type_mismatch",
                    "category": "graph_kernel_binding",
                    "start": start_of_connect,
                    "end": i,
                    "original": full_original,
                    "replacement": replacement_full,
                    "description": f"Changed connect template from '{template_content.strip()}' to '{replacement_type}' causing type mismatch"
                })
            
            # Also generate window size mismatch if applicable
            size_replacement = _generate_window_size_replacement(template_content)
            if size_replacement:
                size_replacement_full = f"connect<{size_replacement}>"
                if size_replacement_full != full_original:
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "connect_template_type_mismatch",
                        "category": "graph_kernel_binding",
                        "start": start_of_connect,
                        "end": i,
                        "original": full_original,
                        "replacement": size_replacement_full,
                        "description": f"Changed connect window size from '{template_content.strip()}' to '{size_replacement}' causing size mismatch"
                    })
            
            pos = i
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    
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
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[filepath] = new_content
    return new_files
