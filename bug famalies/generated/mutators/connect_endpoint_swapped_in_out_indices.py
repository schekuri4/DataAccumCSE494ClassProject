import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF068",
    "bug_type": "connect_endpoint_swapped_in_out_indices",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<stream>",
        "connect<window",
        "connect<>(",
        ".in[",
        ".out["
    ],
    "mutation_strategy": "Swap the source and destination endpoint indices in a connect<> call such that an input port is used as a source and an output port as a destination (e.g., connect<>(k[0].in[0], k[1].out[0]) instead of connect<>(k[0].out[0], k[1].in[0])).",
    "repair_expectation": "Reverse the connect arguments so that the output port is the source and the input port is the destination.",
    "validation_signal": "WSL Vitis/AIE compile failure with direction mismatch or invalid connection topology error.",
    "tags": [
        "connect",
        "direction_mismatch",
        "graph_endpoint_indices",
        "in_out",
        "swapped_endpoints"
    ]
}


def _is_graph_file(filepath):
    """Heuristic: graph headers (.h/.hpp) and graph sources (.cpp/.cc) typically contain 'graph' in name or are .h/.hpp/.cpp/.cc files."""
    lower = filepath.lower()
    # Accept any header or source file that could be a graph file
    extensions = ('.h', '.hpp', '.cpp', '.cc', '.c')
    return any(lower.endswith(ext) for ext in extensions)


# Pattern to match connect<...>( arg1 , arg2 ) where args contain .in[ or .out[
# We capture the full connect call including its two arguments
_CONNECT_PATTERN = re.compile(
    r'(connect\s*<[^>]*>\s*\()'   # group 1: connect<...>(
    r'(\s*)'                       # group 2: optional whitespace after (
    r'([^,]+)'                     # group 3: first argument (source)
    r'(\s*,\s*)'                   # group 4: comma with surrounding whitespace
    r'([^)]+)'                     # group 5: second argument (destination)
    r'(\s*\))'                     # group 6: closing ) with optional whitespace
)


def find_mutation_candidates(project_files):
    candidates = []
    
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        
        for match in _CONNECT_PATTERN.finditer(content):
            src_arg = match.group(3).strip()
            dst_arg = match.group(5).strip()
            
            # We want to find connect calls where source has .out[ and dest has .in[
            # This is the normal/correct pattern. We'll swap them to create the bug.
            src_has_out = '.out[' in src_arg
            dst_has_in = '.in[' in dst_arg
            src_has_in = '.in[' in src_arg
            dst_has_out = '.out[' in dst_arg
            
            # Normal pattern: source is .out[], dest is .in[] — swap to create bug
            if src_has_out and dst_has_in:
                original_text = match.group(0)
                # Swap the two arguments
                swapped_text = (
                    match.group(1) +
                    match.group(2) +
                    dst_arg +  # put destination as source
                    match.group(4) +
                    src_arg +  # put source as destination
                    match.group(6)
                )
                
                candidates.append({
                    "file_path": filepath,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": match.start(),
                    "end": match.end(),
                    "original": original_text,
                    "replacement": swapped_text,
                    "description": (
                        f"Swap source and destination arguments in connect<> call: "
                        f"'{src_arg}' (out) and '{dst_arg}' (in) are swapped so that "
                        f"an input port is used as source and output port as destination."
                    )
                })
            # Already buggy pattern (in as source, out as dest) — skip, not a valid mutation site
            # since we want to introduce bugs, not fix them
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    
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
