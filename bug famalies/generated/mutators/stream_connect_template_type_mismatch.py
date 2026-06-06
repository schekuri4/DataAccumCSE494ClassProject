import re
import copy

BUG_FAMILY = {
    "family_id": "BF105",
    "bug_type": "stream_connect_template_type_mismatch",
    "category": "stream_scalar_interfaces",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<stream>",
        "connect<window",
        "adf::connect<",
        "input_plio",
        "output_plio"
    ],
    "mutation_strategy": "Change the connect template argument from stream to window (or vice versa) for a PLIO-to-kernel connection that uses scalar stream APIs (readincr/writeincr), creating a template type mismatch between the port type and connection type.",
    "repair_expectation": "Restore the correct connect<stream> template argument matching the kernel port and PLIO interface type.",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or port type mismatch in the ADF graph elaboration.",
    "tags": [
        "connect",
        "graph",
        "plio",
        "stream_scalar_interfaces",
        "stream_vs_window",
        "template"
    ]
}


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files, often containing 'graph' in name."""
    lower = file_path.lower()
    if lower.endswith('.h') or lower.endswith('.hpp'):
        return True
    return False


def _has_plio_context(content):
    """Check if file has PLIO-related declarations suggesting stream connections."""
    return ('input_plio' in content or 'output_plio' in content or
            'PLIO' in content)


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match connect<stream> or connect<adf::stream> variants
    # Also matches adf::connect<stream> and adf::connect<adf::stream>
    stream_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*)(stream|adf::stream)(\s*>)'
    )

    # Pattern to match connect<window<N>> or connect<adf::window<N>> variants
    window_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*)(window\s*<[^>]*>|adf::window\s*<[^>]*>)(\s*>)'
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            # Also accept if file contains graph-like content
            if not ('connect<' in content or 'adf::connect<' in content):
                continue

        # Look for stream -> window mutations (preferred when PLIO context exists)
        for match in stream_pattern.finditer(content):
            original_full = match.group(0)
            prefix = match.group(1)
            stream_arg = match.group(2)
            suffix = match.group(3)

            # Replace stream with window<32> (common default window size)
            replacement_arg = "window<32>"
            replacement_full = prefix + replacement_arg + suffix

            candidates.append({
                "file_path": file_path,
                "bug_type": "stream_connect_template_type_mismatch",
                "category": "stream_scalar_interfaces",
                "start": match.start(),
                "end": match.end(),
                "original": original_full,
                "replacement": replacement_full,
                "description": (
                    f"Changed connect template argument from '{stream_arg}' to "
                    f"'window<32>' creating a type mismatch for a stream-based "
                    f"PLIO-to-kernel connection."
                )
            })

        # Look for window -> stream mutations
        for match in window_pattern.finditer(content):
            original_full = match.group(0)
            prefix = match.group(1)
            window_arg = match.group(2)
            suffix = match.group(3)

            # Replace window<N> with stream
            replacement_full = prefix + "stream" + suffix

            candidates.append({
                "file_path": file_path,
                "bug_type": "stream_connect_template_type_mismatch",
                "category": "stream_scalar_interfaces",
                "start": match.start(),
                "end": match.end(),
                "original": original_full,
                "replacement": replacement_full,
                "description": (
                    f"Changed connect template argument from '{window_arg}' to "
                    f"'stream' creating a type mismatch for a window-based "
                    f"connection that should use window interfaces."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
