import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF086",
    "bug_type": "gmio_port_used_as_plio",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_gmio",
        "adf::output_gmio",
        "adf::input_plio",
        "adf::output_plio"
    ],
    "mutation_strategy": "Replace a GMIO port declaration with a PLIO port declaration (or vice versa) while keeping the rest of the graph connections and factory calls unchanged, causing a type mismatch in the connect statements or missing platform port binding.",
    "repair_expectation": "Restore the correct port type (GMIO vs PLIO) to match the intended connection topology and factory creation.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating type mismatch between port declaration and connection or factory call.",
    "tags": [
        "compile_error",
        "gmio",
        "gmio_ports",
        "plio",
        "port_type_swap"
    ]
}

# Mapping of port types to their swapped counterparts
_SWAP_MAP = {
    "adf::input_gmio": "adf::input_plio",
    "adf::output_gmio": "adf::output_plio",
    "adf::input_plio": "adf::input_gmio",
    "adf::output_plio": "adf::output_gmio",
}

# Also handle without adf:: prefix
_SWAP_MAP_NO_NS = {
    "input_gmio": "input_plio",
    "output_gmio": "output_plio",
    "input_plio": "input_gmio",
    "output_plio": "output_gmio",
}

# Pattern to match port declarations like: adf::input_gmio varname; or input_gmio varname;
# Also handles: adf::input_gmio varname = ...;
_PORT_PATTERN = re.compile(
    r'\b((?:adf::)?(?:input_gmio|output_gmio|input_plio|output_plio))\b'
)


def _is_graph_header(filepath):
    """Heuristic to identify graph header files."""
    lower = filepath.lower()
    # Common patterns for graph headers in AIE projects
    if lower.endswith('.h') or lower.endswith('.hpp'):
        return True
    return False


def _get_replacement(original_type):
    """Get the swapped port type."""
    if original_type in _SWAP_MAP:
        return _SWAP_MAP[original_type]
    # Try without namespace
    if original_type in _SWAP_MAP_NO_NS:
        return _SWAP_MAP_NO_NS[original_type]
    # Handle partial namespace cases
    for key, val in _SWAP_MAP.items():
        if original_type == key:
            return val
    return None


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_header(filepath):
            continue

        # Check if file likely contains graph-related content
        if 'graph' not in content.lower() and 'adf' not in content.lower():
            # Relax: still check if it has port types
            pass

        for match in _PORT_PATTERN.finditer(content):
            original_type = match.group(1)
            replacement_type = _get_replacement(original_type)

            if replacement_type is None:
                continue

            start = match.start(1)
            end = match.end(1)

            # Determine direction of swap for description
            if 'gmio' in original_type:
                desc = f"Replace GMIO port declaration '{original_type}' with PLIO '{replacement_type}' to cause type mismatch"
            else:
                desc = f"Replace PLIO port declaration '{original_type}' with GMIO '{replacement_type}' to cause type mismatch"

            candidates.append({
                "file_path": filepath,
                "bug_type": "gmio_port_used_as_plio",
                "category": "gmio_ports",
                "start": start,
                "end": end,
                "original": original_type,
                "replacement": replacement_type,
                "description": desc,
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = project_files[filepath]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]

    new_files[filepath] = new_content
    return new_files
