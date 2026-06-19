import re
import copy

BUG_FAMILY = {
    "family_id": "BF010",
    "bug_type": "circular_include_between_graph_and_kernel_headers",
    "category": "include_headers",
    "target_files": [
        "graph header",
        "kernel header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include \"graph.h\"",
        "#include \"kernels.h\"",
        "adf::graph",
        "void kernel_func("
    ],
    "mutation_strategy": "Add an #include of the graph header inside a kernel header (e.g., to reference a shared type), creating a circular dependency where graph.h includes kernels.h and kernels.h includes graph.h, leading to incomplete type errors or redefinition errors even with header guards due to ordering issues with adf::graph base class.",
    "repair_expectation": "Break the circular dependency by extracting shared types into a separate utility header, or use forward declarations instead of the circular include.",
    "validation_signal": "WSL Vitis/AIE compile failure with incomplete type errors, redefinition errors, or 'expected class-name before { token' due to ordering.",
    "tags": [
        "circular_include",
        "dependency",
        "graph_header",
        "include_headers",
        "kernel_header"
    ]
}


def _identify_graph_headers(project_files):
    """Find files that look like graph headers (contain adf::graph and include a kernel header)."""
    graph_headers = []
    for path, content in project_files.items():
        if not path.endswith('.h') and not path.endswith('.hpp'):
            continue
        if 'adf::graph' in content or 'adf:: graph' in content:
            # Check if it includes a kernel header
            if re.search(r'#include\s*"[^"]*kernel[^"]*\.h[pp]*"', content, re.IGNORECASE):
                graph_headers.append(path)
            elif re.search(r'#include\s*"[^"]*\.h[pp]*"', content):
                # Still a graph header even without explicit kernel include
                graph_headers.append(path)
    return graph_headers


def _identify_kernel_headers(project_files):
    """Find files that look like kernel headers (contain kernel function declarations)."""
    kernel_headers = []
    for path, content in project_files.items():
        if not path.endswith('.h') and not path.endswith('.hpp'):
            continue
        # Look for kernel function declarations
        if re.search(r'void\s+\w+\s*\(', content):
            # Exclude graph headers (those with adf::graph)
            if 'adf::graph' not in content:
                kernel_headers.append(path)
    return kernel_headers


def _get_graph_header_basename(graph_path):
    """Extract the basename of the graph header for use in #include directive."""
    import os
    return os.path.basename(graph_path)


def find_mutation_candidates(project_files):
    candidates = []
    
    graph_headers = _identify_graph_headers(project_files)
    kernel_headers = _identify_kernel_headers(project_files)
    
    # If we can't find explicit graph/kernel headers by content, try by naming convention
    if not graph_headers:
        for path in project_files:
            if (path.endswith('.h') or path.endswith('.hpp')):
                if 'graph' in path.lower() and 'adf' in project_files[path].lower():
                    graph_headers.append(path)
    
    if not kernel_headers:
        for path in project_files:
            if (path.endswith('.h') or path.endswith('.hpp')):
                if 'kernel' in path.lower() and 'adf::graph' not in project_files[path]:
                    kernel_headers.append(path)
    
    for graph_h in graph_headers:
        graph_content = project_files[graph_h]
        graph_basename = _get_graph_header_basename(graph_h)
        
        # Check that graph header includes a kernel header (creating the first direction of dependency)
        for kernel_h in kernel_headers:
            kernel_basename = _get_graph_header_basename(kernel_h)
            kernel_content = project_files[kernel_h]
            
            # Verify graph includes kernel (or at least some include relationship)
            include_pattern = re.compile(r'#include\s*"' + re.escape(kernel_basename) + r'"')
            if not include_pattern.search(graph_content):
                # Also check relative paths
                if kernel_basename not in graph_content:
                    continue
            
            # Now check that kernel header does NOT already include graph header
            graph_include_pattern = re.compile(r'#include\s*"[^"]*' + re.escape(graph_basename) + r'"')
            if graph_include_pattern.search(kernel_content):
                continue  # Already has circular include, skip
            
            # Find insertion point in kernel header - after existing includes or after header guard
            lines = kernel_content.split('\n')
            insert_line_idx = 0
            
            # Find last #include line or header guard
            for i, line in enumerate(lines):
                if line.strip().startswith('#include'):
                    insert_line_idx = i + 1
                elif line.strip().startswith('#ifndef') or line.strip().startswith('#define'):
                    if insert_line_idx == 0:
                        insert_line_idx = i + 1
            
            # If no includes found, insert after first line
            if insert_line_idx == 0:
                insert_line_idx = min(1, len(lines))
            
            # Determine the include path to use for graph header
            import os
            # Try to compute relative path from kernel to graph
            kernel_dir = os.path.dirname(kernel_h)
            if kernel_dir:
                rel_path = os.path.relpath(graph_h, kernel_dir)
            else:
                rel_path = graph_basename
            
            # Build the include line to insert
            include_line = '#include "' + rel_path + '"'
            
            # Calculate start/end positions in the original content
            # We insert after the line at insert_line_idx - 1
            char_pos = 0
            for i in range(insert_line_idx):
                char_pos += len(lines[i]) + 1  # +1 for newline
            anchor_pos = 0
            for i in range(max(0, insert_line_idx - 1)):
                anchor_pos += len(lines[i]) + 1

            original_text = kernel_content[anchor_pos:char_pos]
            replacement_text = original_text + include_line + "\n"

            # For the candidate, use line-based start/end
            candidate = {
                "file_path": kernel_h,
                "bug_type": "circular_include_between_graph_and_kernel_headers",
                "category": "include_headers",
                "start": anchor_pos,
                "end": char_pos,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Add '#include \"{rel_path}\"' in kernel header '{kernel_h}' "
                    f"to create circular dependency with graph header '{graph_h}'. "
                    f"Graph includes kernel, and now kernel includes graph, causing "
                    f"incomplete type errors or redefinition errors during AIE compilation."
                )
            }
            candidates.append(candidate)
    
    # Fallback: if no candidates found with strict matching, try looser matching
    if not candidates:
        # Try to find any pair where one file has adf::graph and includes another,
        # and the other has a void function
        for path_a, content_a in project_files.items():
            if not (path_a.endswith('.h') or path_a.endswith('.hpp')):
                continue
            if 'adf::graph' not in content_a:
                continue
            
            # Find what this graph header includes
            includes = re.findall(r'#include\s*"([^"]+)"', content_a)
            for inc in includes:
                # Find the actual file
                import os
                inc_basename = os.path.basename(inc)
                for path_b, content_b in project_files.items():
                    if not (path_b.endswith('.h') or path_b.endswith('.hpp')):
                        continue
                    if os.path.basename(path_b) != inc_basename:
                        continue
                    if path_b == path_a:
                        continue
                    
                    # Check path_b doesn't already include path_a
                    graph_basename = os.path.basename(path_a)
                    if graph_basename in content_b:
                        continue
                    
                    # Find insertion point
                    lines = content_b.split('\n')
                    insert_line_idx = 0
                    for i, line in enumerate(lines):
                        if line.strip().startswith('#include'):
                            insert_line_idx = i + 1
                        elif line.strip().startswith('#ifndef') or line.strip().startswith('#define'):
                            if insert_line_idx == 0:
                                insert_line_idx = i + 1
                    if insert_line_idx == 0:
                        insert_line_idx = min(1, len(lines))
                    
                    kernel_dir = os.path.dirname(path_b)
                    if kernel_dir:
                        rel_path = os.path.relpath(path_a, kernel_dir)
                    else:
                        rel_path = graph_basename
                    
                    include_line = '#include "' + rel_path + '"'
                    
                    char_pos = 0
                    for i in range(insert_line_idx):
                        char_pos += len(lines[i]) + 1
                    anchor_pos = 0
                    for i in range(max(0, insert_line_idx - 1)):
                        anchor_pos += len(lines[i]) + 1
                    original_text = content_b[anchor_pos:char_pos]

                    candidate = {
                        "file_path": path_b,
                        "bug_type": "circular_include_between_graph_and_kernel_headers",
                        "category": "include_headers",
                        "start": anchor_pos,
                        "end": char_pos,
                        "original": original_text,
                        "replacement": original_text + include_line + "\n",
                        "description": (
                            f"Add '#include \"{rel_path}\"' in '{path_b}' "
                            f"to create circular dependency with '{path_a}'. "
                            f"This causes incomplete type or redefinition errors."
                        )
                    }
                    candidates.append(candidate)
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply the mutation to create a circular include dependency."""
    mutated_files = dict(project_files)  # Shallow copy of the dict
    
    file_path = candidate["file_path"]
    original_content = mutated_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text matches (for non-empty originals)
    if original and original_content[start:end] != original:
        # Try to find it anyway
        idx = original_content.find(original)
        if idx >= 0:
            start = idx
            end = idx + len(original)
    
    # Apply the mutation
    mutated_content = original_content[:start] + replacement + original_content[end:]
    mutated_files[file_path] = mutated_content
    
    return mutated_files
