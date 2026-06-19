import re
import copy

BUG_FAMILY = {
    "family_id": "BF054",
    "bug_type": "gmio_direction_mismatch_in_connect",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_gmio",
        "adf::output_gmio",
        "input_gmio::create",
        "output_gmio::create",
        "connect<"
    ],
    "mutation_strategy": "Declare a GMIO as input_gmio but wire it as a destination in a connect statement (or declare as output_gmio but wire as source), creating a direction conflict.",
    "repair_expectation": "Either change the GMIO declaration to match its usage direction or fix the connect wiring to use the GMIO in the correct role.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating GMIO direction is incompatible with connection endpoint role.",
    "tags": ["connect", "direction", "gmio", "graph_connections", "wiring"]
}


def _is_graph_header(path):
    """Heuristic: graph headers are .h or .hpp files containing adf graph constructs."""
    return path.endswith(('.h', '.hpp'))


def _find_gmio_declarations(content):
    """Find all GMIO declarations with their names and types (input/output)."""
    results = []
    # Match patterns like: adf::input_gmio name or input_gmio name
    # Also handles: adf::input_gmio name = input_gmio::create(...)
    pattern = re.compile(
        r'\b(adf::)?(input_gmio|output_gmio)\s+(\w+)',
        re.MULTILINE
    )
    for m in pattern.finditer(content):
        direction = m.group(2)  # "input_gmio" or "output_gmio"
        name = m.group(3)
        results.append({
            'direction': direction,
            'name': name,
            'match': m
        })
    return results


def _find_connect_statements(content):
    """Find all connect<...>(...) statements and extract source/dest arguments."""
    results = []
    # Match connect< ... >( src , dst ) and plain adf::connect(src, dst)
    pattern = re.compile(
        r'(adf::)?connect(?:\s*<[^>]*>)?\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        re.MULTILINE
    )
    for m in pattern.finditer(content):
        src = m.group(2).strip()
        dst = m.group(3).strip()
        results.append({
            'full_match': m,
            'src': src,
            'dst': dst,
            'start': m.start(),
            'end': m.end(),
            'original': m.group(0)
        })
    return results


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        gmio_decls = _find_gmio_declarations(content)
        if not gmio_decls:
            continue

        connect_stmts = _find_connect_statements(content)
        if not connect_stmts:
            continue

        # Build name->direction map
        gmio_map = {d['name']: d['direction'] for d in gmio_decls}

        for conn in connect_stmts:
            src = conn['src']
            dst = conn['dst']

            # Check if an input_gmio is used as source (correct) - we can swap to make it dest
            for gmio_name, direction in gmio_map.items():
                if direction == 'input_gmio' and gmio_name in src and gmio_name not in dst:
                    # input_gmio correctly used as source; swap src and dst to create mismatch
                    # (input_gmio becomes destination = direction conflict)
                    original_text = conn['original']
                    # Swap source and destination
                    new_text = original_text[:original_text.index('(')+1]
                    # Rebuild with swapped args
                    inner_start = original_text.index('(') + 1
                    inner_end = original_text.rindex(')')
                    inner = original_text[inner_start:inner_end]
                    parts = inner.split(',', 1)
                    if len(parts) == 2:
                        swapped_inner = parts[1].strip() + ', ' + parts[0].strip()
                        replacement = original_text[:inner_start] + swapped_inner + original_text[inner_end:]

                        candidates.append({
                            'file_path': file_path,
                            'bug_type': 'gmio_direction_mismatch_in_connect',
                            'category': 'graph_connections',
                            'start': conn['start'],
                            'end': conn['end'],
                            'original': original_text,
                            'replacement': replacement,
                            'description': f"Swapped connect arguments so input_gmio '{gmio_name}' is used as destination, creating a direction mismatch."
                        })
                    break

                elif direction == 'output_gmio' and gmio_name in dst and gmio_name not in src:
                    # output_gmio correctly used as destination; swap to make it source
                    original_text = conn['original']
                    inner_start = original_text.index('(') + 1
                    inner_end = original_text.rindex(')')
                    inner = original_text[inner_start:inner_end]
                    parts = inner.split(',', 1)
                    if len(parts) == 2:
                        swapped_inner = parts[1].strip() + ', ' + parts[0].strip()
                        replacement = original_text[:inner_start] + swapped_inner + original_text[inner_end:]

                        candidates.append({
                            'file_path': file_path,
                            'bug_type': 'gmio_direction_mismatch_in_connect',
                            'category': 'graph_connections',
                            'start': conn['start'],
                            'end': conn['end'],
                            'original': original_text,
                            'replacement': replacement,
                            'description': f"Swapped connect arguments so output_gmio '{gmio_name}' is used as source, creating a direction mismatch."
                        })
                    break

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate['file_path']
    content = new_files[file_path]

    original = candidate['original']
    replacement = candidate['replacement']
    start = candidate['start']
    end = candidate['end']

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
