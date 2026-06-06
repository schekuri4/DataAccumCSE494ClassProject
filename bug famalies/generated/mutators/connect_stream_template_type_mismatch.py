import re
import copy

BUG_FAMILY = {
    "family_id": "BF117",
    "bug_type": "connect_stream_template_type_mismatch",
    "category": "stream_vector_interfaces",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<stream>",
        "connect<window<",
        "adf::connect<",
        "kernel::create"
    ],
    "mutation_strategy": "Change a connect<stream> to connect<window<N>> or vice versa for a port that the kernel expects as a stream pointer (input_stream/output_stream), causing a template mismatch between the graph connection type and the kernel's port interface.",
    "repair_expectation": "Restore the correct connect template (stream vs window) to match the kernel's declared port type (stream pointer vs window buffer).",
    "validation_signal": "WSL Vitis/AIE compile failure with port type mismatch or connection template incompatibility error.",
    "tags": [
        "connect_template",
        "graph_topology",
        "stream_vector_interfaces",
        "stream_window_confusion"
    ]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for graph files
    if 'graph' in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might contain graph definitions
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match connect<stream> connections
    # Matches: connect<stream>, adf::connect<stream>
    stream_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*stream\s*>)'
    )

    # Pattern to match connect<window<N>> connections
    # Matches: connect<window<128>>, adf::connect<window<256>>
    window_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*window\s*<\s*(\d+)\s*>\s*>)'
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Find connect<stream> candidates -> mutate to connect<window<128>>
        for match in stream_pattern.finditer(content):
            original = match.group(1)
            start = match.start(1)
            end = match.end(1)

            # Determine prefix (adf:: or not)
            prefix = "adf::" if "adf::" in original else ""
            replacement = f"{prefix}connect<window<128>>"

            candidates.append({
                "file_path": filepath,
                "bug_type": "connect_stream_template_type_mismatch",
                "category": "stream_vector_interfaces",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed '{original}' to '{replacement}' causing a template "
                    f"mismatch for a port expecting a stream interface."
                )
            })

        # Find connect<window<N>> candidates -> mutate to connect<stream>
        for match in window_pattern.finditer(content):
            original = match.group(1)
            start = match.start(1)
            end = match.end(1)

            # Determine prefix
            prefix = "adf::" if "adf::" in original else ""
            replacement = f"{prefix}connect<stream>"

            candidates.append({
                "file_path": filepath,
                "bug_type": "connect_stream_template_type_mismatch",
                "category": "stream_vector_interfaces",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed '{original}' to '{replacement}' causing a template "
                    f"mismatch for a port expecting a window buffer interface."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)

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

    new_project_files[filepath] = new_content
    return new_project_files
