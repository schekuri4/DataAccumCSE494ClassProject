import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF076",
    "bug_type": "plio_port_index_out_of_range",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [".in[", ".out[", "connect<>"],
    "mutation_strategy": "Use an out-of-range port index when connecting a PLIO to a kernel, e.g., referencing kernel.in[2] when the kernel only has 2 input ports (valid indices 0 and 1), or using a negative index.",
    "repair_expectation": "Correct the port index to a valid value within the declared range of the kernel's input or output ports.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about port index out of bounds or no port at the specified index.",
    "tags": ["connection", "out_of_range", "plio", "plio_ports", "port_index"],
}


def _is_graph_file(path: str) -> bool:
    """Heuristic to identify graph header or source files."""
    lower = path.lower()
    # Common patterns for AIE graph files
    if "graph" in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might be graph files
    if lower.endswith((".h", ".hpp", ".cpp", ".cc")):
        return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match port index access like .in[0], .out[1], etc.
    # Captures: prefix (including .in or .out), the bracket with index, and the index value
    port_pattern = re.compile(
        r'(\.\s*(in|out)\s*\[)\s*(\d+)\s*(\])'
    )

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        for match in port_pattern.finditer(content):
            prefix = match.group(1)  # e.g., ".in["
            port_dir = match.group(2)  # "in" or "out"
            index_str = match.group(3)  # e.g., "0"
            closing = match.group(4)  # "]"

            index_val = int(index_str)

            # Create an out-of-range index by adding a significant offset
            # This makes the index clearly invalid
            if index_val <= 1:
                # For small indices (0 or 1), bump to something clearly out of range
                new_index = index_val + 5
            else:
                # For larger indices, add 3 to go out of range
                new_index = index_val + 3

            original_text = match.group(0)  # full match like ".in[0]"
            replacement_text = f"{prefix}{new_index}{closing}"

            start = match.start()
            end = match.end()

            description = (
                f"Changed port index from .{port_dir}[{index_val}] to "
                f".{port_dir}[{new_index}] to create an out-of-range port index error."
            )

            candidates.append({
                "file_path": file_path,
                "bug_type": "plio_port_index_out_of_range",
                "category": "plio_ports",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": description,
            })

    return candidates


def apply_mutation(
    project_files: dict[str, str], candidate: dict[str, object]
) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

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

    new_files[file_path] = new_content
    return new_files
