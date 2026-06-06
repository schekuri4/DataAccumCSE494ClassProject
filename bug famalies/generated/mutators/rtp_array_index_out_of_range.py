import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF066",
    "bug_type": "rtp_array_index_out_of_range",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<parameter>(",
        "async(",
        "port<direction::in> rtp_in[",
        "adf::connect<adf::parameter>"
    ],
    "mutation_strategy": "Declare an RTP port array of size P on a kernel but reference index >= P in connect<parameter> or async/sync RTP update calls in the graph constructor or test harness.",
    "repair_expectation": "Use a valid RTP port index within [0, P-1] or add additional RTP port declarations to the kernel.",
    "validation_signal": "WSL Vitis/AIE compile failure with RTP port index out of range or unresolved parameter connection.",
    "tags": ["array_index", "graph_endpoint_indices", "out_of_range", "parameter", "rtp"]
}


def _is_graph_file(path: str) -> bool:
    """Heuristic: graph headers (.h/.hpp) and graph sources (.cpp/.cc) typically contain 'graph' in name or content."""
    lower = path.lower()
    exts = ('.h', '.hpp', '.cpp', '.cc', '.c')
    return any(lower.endswith(ext) for ext in exts)


def _find_rtp_array_sizes(content: str) -> dict[str, int]:
    """Find RTP port array declarations like: port<direction::in> name[N]; or adf::port<...> name[N];"""
    pattern = re.compile(
        r'(?:adf::)?port\s*<\s*(?:adf::)?direction\s*::\s*(?:in|out|inout)\s*>\s*(\w+)\s*\[\s*(\d+)\s*\]'
    )
    results = {}
    for m in pattern.finditer(content):
        arr_name = m.group(1)
        arr_size = int(m.group(2))
        results[arr_name] = arr_size
    return results


def _find_rtp_index_usages(content: str, arr_names: list[str]) -> list[dict[str, Any]]:
    """Find usages of RTP array indices in connect<parameter>, async, sync calls."""
    candidates = []
    # Pattern: array_name[index] used in various contexts
    for arr_name in arr_names:
        # Match arr_name[digits] anywhere (we'll validate context separately)
        pattern = re.compile(
            r'(' + re.escape(arr_name) + r')\[(\d+)\]'
        )
        for m in pattern.finditer(content):
            idx = int(m.group(2))
            start = m.start()
            end = m.end()
            # Check surrounding context for RTP-related usage
            # Look at the line containing this match
            line_start = content.rfind('\n', 0, start) + 1
            line_end = content.find('\n', end)
            if line_end == -1:
                line_end = len(content)
            line = content[line_start:line_end]
            # Check if line contains relevant keywords
            rtp_context = any(kw in line for kw in [
                'connect<parameter>', 'connect<adf::parameter>',
                'adf::connect<adf::parameter>', 'adf::connect<parameter>',
                'async(', 'sync(', 'update(', 'read(',
                'connect< parameter>', 'connect <parameter>'
            ])
            if rtp_context:
                candidates.append({
                    'arr_name': arr_name,
                    'index': idx,
                    'start': start,
                    'end': end,
                    'original': m.group(0),
                })
    return candidates


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Find RTP array declarations in this file
        rtp_arrays = _find_rtp_array_sizes(content)
        if not rtp_arrays:
            continue

        # Find usages of these arrays with indices
        usages = _find_rtp_index_usages(content, list(rtp_arrays.keys()))

        for usage in usages:
            arr_name = usage['arr_name']
            current_idx = usage['index']
            arr_size = rtp_arrays[arr_name]

            # Only mutate if current index is valid (within range)
            if current_idx < arr_size:
                # Create out-of-range index
                new_idx = arr_size  # exactly one past the end
                replacement = f"{arr_name}[{new_idx}]"
                description = (
                    f"Changed RTP port index from {arr_name}[{current_idx}] to "
                    f"{arr_name}[{new_idx}] (array size is {arr_size}, "
                    f"so index {new_idx} is out of range)."
                )
                candidates.append({
                    'file_path': file_path,
                    'bug_type': BUG_FAMILY['bug_type'],
                    'category': BUG_FAMILY['category'],
                    'start': usage['start'],
                    'end': usage['end'],
                    'original': usage['original'],
                    'replacement': replacement,
                    'description': description,
                })

    # If no context-based matches found, try a broader approach:
    # look for any array index usage near connect<parameter> patterns
    if not candidates:
        for file_path, content in project_files.items():
            if not _is_graph_file(file_path):
                continue

            rtp_arrays = _find_rtp_array_sizes(content)
            if not rtp_arrays:
                continue

            # Broader: find any usage of the rtp array names with indices
            for arr_name, arr_size in rtp_arrays.items():
                pattern = re.compile(
                    r'(' + re.escape(arr_name) + r')\[(\d+)\]'
                )
                for m in pattern.finditer(content):
                    current_idx = int(m.group(2))
                    if current_idx < arr_size:
                        new_idx = arr_size
                        replacement = f"{arr_name}[{new_idx}]"
                        description = (
                            f"Changed RTP port index from {arr_name}[{current_idx}] to "
                            f"{arr_name}[{new_idx}] (array size is {arr_size}, "
                            f"so index {new_idx} is out of range)."
                        )
                        candidates.append({
                            'file_path': file_path,
                            'bug_type': BUG_FAMILY['bug_type'],
                            'category': BUG_FAMILY['category'],
                            'start': m.start(),
                            'end': m.end(),
                            'original': m.group(0),
                            'replacement': replacement,
                            'description': description,
                        })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate['file_path']
    content = new_files[file_path]

    start = candidate['start']
    end = candidate['end']
    original = candidate['original']

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate['replacement'] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate['replacement'], 1)

    new_files[file_path] = new_content
    return new_files
