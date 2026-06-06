import re
import copy


BUG_FAMILY = {
    "family_id": "BF099",
    "bug_type": "rtp_port_not_connected_in_graph",
    "category": "rtp_parameters",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<parameter>(",
        "port<direction::in>",
        "port<direction::out>",
        "kernel::create"
    ],
    "mutation_strategy": "Declare an RTP port in the graph class but omit the connect<parameter>() call in the graph constructor, leaving the RTP port unconnected and causing a compile-time error about unconnected ports.",
    "repair_expectation": "Add the missing connect<parameter>() call to properly connect the declared RTP port to the kernel's corresponding input/output port.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about unconnected port or incomplete graph connectivity.",
    "tags": [
        "compile_time",
        "graph_constructor",
        "port",
        "rtp",
        "rtp_parameters",
        "unconnected"
    ]
}


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files likely containing graph definitions."""
    return file_path.endswith(('.h', '.hpp', '.hh'))


def find_mutation_candidates(project_files):
    """Find connect<parameter>() calls in graph header files that can be removed."""
    candidates = []

    # Pattern to match connect<parameter>(...) statements (full line or statement)
    connect_param_pattern = re.compile(
        r'(?P<full>[ \t]*connect\s*<\s*parameter\s*>\s*\([^)]*\)\s*;[ \t]*(?:\n)?)',
        re.MULTILINE
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Check if this file looks like a graph header (contains kernel::create or graph class)
        has_graph_indicators = (
            'kernel::create' in content or
            'adf::graph' in content or
            'graph' in content
        )
        if not has_graph_indicators:
            continue

        # Find all connect<parameter>() calls
        for match in connect_param_pattern.finditer(content):
            full_text = match.group('full')
            start = match.start()
            end = match.end()

            candidate = {
                "file_path": file_path,
                "bug_type": "rtp_port_not_connected_in_graph",
                "category": "rtp_parameters",
                "start": start,
                "end": end,
                "original": full_text,
                "replacement": "",  # Remove the connect<parameter>() call entirely
                "description": (
                    f"Remove connect<parameter>() call in '{file_path}' "
                    f"to leave an RTP port unconnected, causing a compile-time error. "
                    f"Removed: {full_text.strip()}"
                )
            }
            candidates.append(candidate)

    # If the simple pattern didn't match, try a more permissive pattern
    # that handles multi-line connect<parameter> calls
    if not candidates:
        connect_param_multiline = re.compile(
            r'(?P<full>[ \t]*connect\s*<\s*parameter\s*>\s*\((?:[^;]*?)\)\s*;[ \t]*(?:\n)?)',
            re.MULTILINE | re.DOTALL
        )

        for file_path, content in project_files.items():
            if not _is_graph_header(file_path):
                continue

            has_graph_indicators = (
                'kernel::create' in content or
                'adf::graph' in content or
                'graph' in content
            )
            if not has_graph_indicators:
                continue

            for match in connect_param_multiline.finditer(content):
                full_text = match.group('full')
                start = match.start()
                end = match.end()

                candidate = {
                    "file_path": file_path,
                    "bug_type": "rtp_port_not_connected_in_graph",
                    "category": "rtp_parameters",
                    "start": start,
                    "end": end,
                    "original": full_text,
                    "replacement": "",
                    "description": (
                        f"Remove connect<parameter>() call in '{file_path}' "
                        f"to leave an RTP port unconnected, causing a compile-time error. "
                        f"Removed: {full_text.strip()}"
                    )
                }
                candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    """Apply the mutation by removing the connect<parameter>() call."""
    new_project_files = dict(project_files)

    file_path = candidate["file_path"]
    original_content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if original_content[start:end] == original:
        new_content = original_content[:start] + replacement + original_content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = original_content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
