BUG_FAMILY = {
    "family_id": "BF064",
    "bug_type": "kernel_in_port_index_overflow",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["connect<>(", "k[0].in[", "k[0].out[", ".in["],
    "mutation_strategy": "In a connect<> statement, reference a kernel input port index that exceeds the number of input ports defined in the kernel function signature (e.g., k[0].in[2] when the kernel only has 2 inputs, valid indices 0 and 1).",
    "repair_expectation": "Change the port index to match the actual number of input ports declared in the kernel function signature.",
    "validation_signal": "WSL Vitis/AIE compile failure with port index out of range or unresolved port connection error.",
    "tags": ["connect", "graph_endpoint_indices", "in_port", "index_overflow", "kernel_port"],
}

import re
import copy


def _is_graph_file(path):
    """Heuristic: graph headers (.h/.hpp) or graph source (.cpp/.cc) files."""
    lower = path.lower()
    # Look for 'graph' in filename or path, or accept any header/source
    if 'graph' in lower:
        return True
    # Also consider any .h/.hpp/.cpp/.cc that contains connect<> patterns
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to find connect<...>(..., kernel_ref.in[N]) or similar
    # We look for .in[<number>] within connect statements or general port references
    # General pattern: something.in[digits]
    connect_pattern = re.compile(
        r'(connect\s*<[^>]*>\s*\([^)]*?)'  # prefix up to the .in[ part
        r'(\.\s*in\s*\[\s*)(\d+)(\s*\])',  # .in[N]
        re.DOTALL
    )

    # Broader pattern: any .in[N] usage (covers assignments, connect, etc.)
    in_port_pattern = re.compile(
        r'(\w+(?:\s*\[\s*\d+\s*\])?\s*\.\s*in\s*\[\s*)(\d+)(\s*\])'
    )

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Check if file has any relevant patterns
        if '.in[' not in content and '.in [' not in content:
            continue

        # First try to find .in[N] within connect<> statements
        for m in connect_pattern.finditer(content):
            idx_str = m.group(3)
            idx = int(idx_str)
            # Create overflow by incrementing the index
            new_idx = idx + 1
            
            full_match_start = m.start()
            # The .in[N] part starts at group(2) start
            in_start = m.start(2)
            in_end = m.end(4)
            original = m.group(2) + m.group(3) + m.group(4)
            replacement = m.group(2) + str(new_idx) + m.group(4)

            candidates.append({
                "file_path": file_path,
                "bug_type": "kernel_in_port_index_overflow",
                "category": "graph_endpoint_indices",
                "start": in_start,
                "end": in_end,
                "original": original,
                "replacement": replacement,
                "description": f"Changed .in[{idx}] to .in[{new_idx}] in connect<> statement, causing port index overflow.",
            })

        # Also find standalone .in[N] references not already captured
        seen_positions = {c["start"] for c in candidates if c["file_path"] == file_path}

        for m in in_port_pattern.finditer(content):
            start = m.start()
            if start in seen_positions:
                continue
            # Check we haven't already covered this via connect pattern
            in_start = m.start(1)
            if in_start in seen_positions:
                continue

            idx_str = m.group(2)
            idx = int(idx_str)
            new_idx = idx + 1

            original = m.group(1) + m.group(2) + m.group(3)
            replacement = m.group(1) + str(new_idx) + m.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": "kernel_in_port_index_overflow",
                "category": "graph_endpoint_indices",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Changed .in[{idx}] to .in[{new_idx}], causing kernel input port index overflow.",
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
