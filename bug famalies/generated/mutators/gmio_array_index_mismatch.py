import re
import copy
from typing import Any


BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF063",
    "bug_type": "gmio_array_index_mismatch",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_gmio gm_in[",
        "output_gmio gm_out[",
        "GMIO::create"
    ],
    "mutation_strategy": "Declare a GMIO array of size M but reference index M or higher in connect<> statements or GMIO::create assignments within the graph constructor.",
    "repair_expectation": "Correct the GMIO index to be within [0, M-1] or expand the GMIO array declaration.",
    "validation_signal": "WSL Vitis/AIE compile failure with array index out of bounds or undefined behavior caught at graph elaboration time.",
    "tags": [
        "array_index",
        "gmio",
        "graph_constructor",
        "graph_endpoint_indices",
        "out_of_range"
    ]
}


def _is_graph_file(path: str) -> bool:
    """Heuristic: graph headers (.h/.hpp) or source files (.cpp/.cc) that likely contain graph definitions."""
    lower = path.lower()
    # Accept common graph file patterns
    if any(ext in lower for ext in ['.h', '.hpp', '.cpp', '.cc']):
        return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to find GMIO array declarations with their sizes
    # e.g., input_gmio gm_in[2]; or output_gmio gm_out[4];
    decl_pattern = re.compile(
        r'\b((?:input_gmio|output_gmio)\s+(gm_\w+))\s*\[\s*(\d+)\s*\]'
    )

    # Pattern to find usage of GMIO arrays with indices
    # e.g., gm_in[0], gm_out[1], used in connect<> or GMIO::create assignments
    usage_pattern = re.compile(
        r'(gm_\w+)\s*\[\s*(\d+)\s*\]'
    )

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Check if file contains any of our match targets
        has_match_target = any(mt in content for mt in BUG_FAMILY["match_targets"])
        if not has_match_target:
            continue

        # Find all GMIO array declarations and their sizes
        declarations: dict[str, int] = {}
        for m in decl_pattern.finditer(content):
            array_name = m.group(2)
            array_size = int(m.group(3))
            declarations[array_name] = array_size

        if not declarations:
            continue

        # Find all usages of these arrays with indices
        for m in usage_pattern.finditer(content):
            array_name = m.group(1)
            current_index = int(m.group(2))

            if array_name not in declarations:
                continue

            array_size = declarations[array_name]

            # We want to mutate a valid index to an out-of-bounds index
            # Only mutate if current index is valid (within bounds)
            if current_index >= array_size:
                continue  # Already out of bounds, skip

            # The out-of-bounds index we'll use: array_size (one past the end)
            oob_index = array_size

            original_text = f"{array_name}[{current_index}]"
            replacement_text = f"{array_name}[{oob_index}]"

            # Find the exact position in the content
            start = m.start()
            end = m.end()
            actual_original = content[start:end]

            # Reconstruct to handle potential whitespace in the match
            # Replace just the index number
            idx_start = content.index('[', start) + 1
            idx_end = content.index(']', idx_start)
            idx_original = content[idx_start:idx_end].strip()

            # Use the full matched text for original/replacement
            full_original = content[start:end]
            full_replacement = f"{array_name}[{oob_index}]"

            candidate = {
                "file_path": file_path,
                "bug_type": "gmio_array_index_mismatch",
                "category": "graph_endpoint_indices",
                "start": start,
                "end": end,
                "original": full_original,
                "replacement": full_replacement,
                "description": (
                    f"Changed {array_name} index from {current_index} to {oob_index} "
                    f"(array size is {array_size}), causing out-of-bounds access."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
