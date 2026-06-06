import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF067",
    "bug_type": "graph_in_out_port_index_mismatch_in_hierarchy",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["port<input>", "port<output>", "in[", "out[", "adf::port<"],
    "mutation_strategy": "In a hierarchical graph where a subgraph exposes in[N]/out[M] ports, connect to an index beyond N or M from the parent graph (e.g., subgraph.in[3] when subgraph only declares in[0..2]).",
    "repair_expectation": "Correct the hierarchical port index to match the subgraph's declared port array size.",
    "validation_signal": "WSL Vitis/AIE compile failure with port index exceeds declared graph port array size.",
    "tags": ["graph_endpoint_indices", "hierarchical_graph", "in_out", "port_index", "subgraph"],
}


def _is_graph_file(path: str) -> bool:
    """Heuristic: graph headers (.h/.hpp) or source files (.cpp/.cc) that likely contain graph definitions."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.h', '.hpp', '.hxx', '.cpp', '.cc', '.cxx'))


def _find_subgraph_port_accesses(content: str):
    """
    Find patterns like: identifier.in[N] or identifier.out[N]
    These represent hierarchical port accesses on subgraph instances.
    Returns list of (match_obj, direction, index_int, start, end)
    """
    # Match: <identifier>.<in|out>[<number>]
    # The identifier should look like a subgraph instance (not 'this' or standalone)
    pattern = re.compile(
        r'(\b[a-zA-Z_]\w*)\s*\.\s*(in|out)\s*\[\s*(\d+)\s*\]'
    )
    results = []
    for m in pattern.finditer(content):
        instance_name = m.group(1)
        # Skip 'this' or common non-subgraph identifiers
        if instance_name in ('this',):
            continue
        direction = m.group(2)
        index_val = int(m.group(3))
        results.append({
            'match': m,
            'instance': instance_name,
            'direction': direction,
            'index': index_val,
            'start': m.start(),
            'end': m.end(),
            'full_text': m.group(0),
        })
    return results


def _find_port_declarations(content: str):
    """
    Find port array declarations like:
    port<input> in[N]; or adf::port<input> in[N];
    port<output> out[M]; or adf::port<output> out[M];
    Returns dict: {direction: max_declared_size} based on array sizes found.
    """
    pattern = re.compile(
        r'(?:adf::)?port\s*<\s*(input|output)\s*>\s+(in|out)\s*\[\s*(\d+)\s*\]'
    )
    declarations = {}
    for m in pattern.finditer(content):
        direction = m.group(2)  # 'in' or 'out'
        size = int(m.group(3))
        # Keep the maximum declared size for each direction
        if direction not in declarations or size > declarations[direction]:
            declarations[direction] = size
    return declarations


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    # First pass: identify files and gather port declarations per file
    graph_files = {p: c for p, c in project_files.items() if _is_graph_file(p)}

    # Collect all port declarations across all graph files (subgraph classes)
    # Map: class_name -> {direction: size}
    # Also collect instance type mappings
    # For simplicity, we look for subgraph port accesses and mutate the index

    for file_path, content in graph_files.items():
        accesses = _find_subgraph_port_accesses(content)
        if not accesses:
            continue

        # Try to find port declarations in the same or other files for the subgraph
        # For a robust approach: find all port declarations in all files
        all_declarations = {}
        for fp, fc in graph_files.items():
            decls = _find_port_declarations(fc)
            if decls:
                # We don't know which class they belong to without full parsing,
                # so we'll use them as a general reference
                for d, s in decls.items():
                    if d not in all_declarations or s > all_declarations[d]:
                        all_declarations[d] = s

        for access in accesses:
            direction = access['direction']
            current_index = access['index']

            # Determine the new (invalid) index: current max + 1 or current + 1
            # If we know the declared size, go one beyond it
            declared_size = all_declarations.get(direction)

            if declared_size is not None:
                # Make the index exceed the declared size
                new_index = declared_size  # e.g., if size is 3, valid indices are 0..2, so 3 is invalid
                if current_index >= declared_size:
                    # Already out of bounds? Skip or increment further
                    new_index = current_index + 1
            else:
                # No declaration found; just increment the index by 1
                new_index = current_index + 1

            if new_index == current_index:
                continue

            # Build the replacement string
            original_text = access['full_text']
            # Replace the index in the original text
            replacement_text = re.sub(
                r'(\.\s*(?:in|out)\s*\[\s*)\d+(\s*\])',
                lambda m_inner: m_inner.group(1) + str(new_index) + m_inner.group(2),
                original_text
            )

            if replacement_text == original_text:
                continue

            candidates.append({
                'file_path': file_path,
                'bug_type': BUG_FAMILY['bug_type'],
                'category': BUG_FAMILY['category'],
                'start': access['start'],
                'end': access['end'],
                'original': original_text,
                'replacement': replacement_text,
                'description': (
                    f"Changed hierarchical port index from "
                    f"{access['instance']}.{direction}[{current_index}] to "
                    f"{access['instance']}.{direction}[{new_index}], "
                    f"exceeding the subgraph's declared port array size."
                ),
            })

    # Deduplicate: if same location appears multiple times, keep first
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c['file_path'], c['start'], c['end'])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    return unique_candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate['file_path']
    original_content = new_files[file_path]

    start = candidate['start']
    end = candidate['end']
    original_text = candidate['original']
    replacement_text = candidate['replacement']

    # Verify the original text is at the expected position
    if original_content[start:end] == original_text:
        new_content = original_content[:start] + replacement_text + original_content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = original_content.replace(original_text, replacement_text, 1)

    new_files[file_path] = new_content
    return new_files
