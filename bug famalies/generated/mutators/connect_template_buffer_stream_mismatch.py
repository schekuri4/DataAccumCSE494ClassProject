import re
import copy

BUG_FAMILY = {
    "family_id": "BF135",
    "bug_type": "connect_template_buffer_stream_mismatch",
    "category": "buffer_interfaces",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<buffer,",
        "connect<stream,",
        "connect<window<",
        "adf::connect<"
    ],
    "mutation_strategy": "Change a connect<> template from buffer to stream (or vice versa) in the graph while the kernel port is declared as input_buffer/output_buffer, creating a connection type mismatch.",
    "repair_expectation": "Match the connect<> template arguments to the kernel's declared port interface type (buffer for input_buffer/output_buffer kernels).",
    "validation_signal": "WSL Vitis/AIE compile failure with connection type incompatibility or port type mismatch error.",
    "tags": [
        "buffer_interfaces",
        "buffer_stream_mismatch",
        "connect",
        "graph",
        "port_type"
    ]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for graph files
    if 'graph' in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might be graph files
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Patterns to match connect<> template instantiations
    # Match connect<buffer, buffer>, connect<buffer, stream>, connect<stream, stream>, etc.
    # Also match with adf:: prefix and window<N> variants
    connect_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*)'
        r'(buffer|stream|window\s*<[^>]*>)'
        r'(\s*,\s*)'
        r'(buffer|stream|window\s*<[^>]*>)'
        r'(\s*>)'
    )

    # Also match single-type connect patterns like connect<buffer, ...> with possible sizes
    # More general pattern
    general_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*)'
        r'(buffer|stream|window\s*<\s*\d+\s*>)'
        r'(\s*,\s*)'
        r'(buffer|stream|window\s*<\s*\d+\s*>)'
        r'(\s*>)'
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        for match in general_pattern.finditer(content):
            prefix = match.group(1)
            first_type = match.group(2)
            separator = match.group(3)
            second_type = match.group(4)
            suffix = match.group(5)

            original = match.group(0)
            start = match.start()
            end = match.end()

            # Determine mutation: swap buffer <-> stream
            first_base = first_type.strip().split('<')[0].strip()
            second_base = second_type.strip().split('<')[0].strip()

            new_first = first_type
            new_second = second_type

            if first_base == 'buffer':
                new_first = 'stream'
            elif first_base == 'stream':
                new_first = 'buffer'

            if second_base == 'buffer':
                new_second = 'stream'
            elif second_base == 'stream':
                new_second = 'buffer'

            # Only mutate if something actually changes
            if new_first == first_type and new_second == second_type:
                continue

            replacement = prefix + new_first + separator + new_second + suffix

            candidates.append({
                "file_path": filepath,
                "bug_type": "connect_template_buffer_stream_mismatch",
                "category": "buffer_interfaces",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed connect<> template from '{first_type}, {second_type}' "
                    f"to '{new_first}, {new_second}' creating a buffer/stream type mismatch "
                    f"in {filepath}"
                )
            })

    # If no two-argument connect found, try simpler patterns
    if not candidates:
        # Try matching connect<buffer or connect<stream with single arg or partial
        simple_pattern = re.compile(
            r'((?:adf::)?connect\s*<\s*)(buffer|stream)(\s*[,>])'
        )
        for filepath, content in project_files.items():
            if not _is_graph_file(filepath):
                continue

            for match in simple_pattern.finditer(content):
                prefix = match.group(1)
                conn_type = match.group(2)
                suffix = match.group(3)

                original = match.group(0)
                start = match.start()
                end = match.end()

                if conn_type == 'buffer':
                    new_type = 'stream'
                else:
                    new_type = 'buffer'

                replacement = prefix + new_type + suffix

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "connect_template_buffer_stream_mismatch",
                    "category": "buffer_interfaces",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Changed connect<> template type from '{conn_type}' to '{new_type}' "
                        f"creating a buffer/stream type mismatch in {filepath}"
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[filepath] = new_content
    return new_files
