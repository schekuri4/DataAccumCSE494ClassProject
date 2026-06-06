import re
import copy


BUG_FAMILY = {
    "family_id": "BF089",
    "bug_type": "gmio_port_not_connected",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["adf::input_gmio", "adf::output_gmio", "adf::connect"],
    "mutation_strategy": "Declare a GMIO port in the graph class and create it via the factory, but omit the adf::connect statement that links it to a kernel port, leaving the GMIO port dangling. The ADF compiler enforces that all declared ports must be connected.",
    "repair_expectation": "Add the missing adf::connect statement to link the GMIO port to the appropriate kernel input or output port.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating unconnected port or dangling GMIO in the graph.",
    "tags": [
        "compile_error",
        "gmio",
        "gmio_ports",
        "missing_connect",
        "unconnected_port"
    ]
}


def _is_graph_header(path):
    """Heuristic: graph header files are .h/.hpp files likely containing graph definitions."""
    return path.endswith(('.h', '.hpp')) and 'graph' in path.lower()


def _find_gmio_port_names(content):
    """Find declared GMIO port member names (input_gmio or output_gmio)."""
    # Match patterns like: adf::input_gmio gm_in; or input_gmio gm_in;
    pattern = re.compile(
        r'(?:adf::)?(input_gmio|output_gmio)\s+(\w+)\s*;'
    )
    return pattern.findall(content)


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Find all GMIO port declarations
        gmio_ports = _find_gmio_port_names(content)
        if not gmio_ports:
            continue

        # For each GMIO port, find connect statements that reference it
        for gmio_type, port_name in gmio_ports:
            # Find adf::connect statements that use this port name
            # Patterns like: adf::connect<...>(port_name.out[0], ...) or adf::connect<...>(..., port_name.in[0])
            # Also: connect<...>(port_name.out[0], ...) or connect(port_name..., ...)
            connect_pattern = re.compile(
                r'([ \t]*(?:adf::)?connect\s*<[^>]*>\s*\([^)]*\b'
                + re.escape(port_name)
                + r'\b[^)]*\)\s*;[ \t]*(?://[^\n]*)?)',
                re.MULTILINE
            )

            # Also try simpler connect pattern without template args
            connect_pattern_simple = re.compile(
                r'([ \t]*(?:adf::)?connect\s*\([^)]*\b'
                + re.escape(port_name)
                + r'\b[^)]*\)\s*;[ \t]*(?://[^\n]*)?)',
                re.MULTILINE
            )

            matches = list(connect_pattern.finditer(content))
            matches += list(connect_pattern_simple.finditer(content))

            # Deduplicate by start position
            seen_starts = set()
            unique_matches = []
            for m in matches:
                if m.start() not in seen_starts:
                    seen_starts.add(m.start())
                    unique_matches.append(m)

            for m in unique_matches:
                original_line = m.group(0)
                # The replacement is to comment out / remove the connect statement
                # We'll remove the line entirely (replace with empty or a comment)
                # To leave the port dangling, we just remove the connect statement
                replacement = ""

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "gmio_port_not_connected",
                    "category": "gmio_ports",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_line,
                    "replacement": replacement,
                    "description": (
                        f"Remove adf::connect statement for GMIO port '{port_name}' "
                        f"({gmio_type}), leaving it dangling/unconnected. "
                        f"This should cause an ADF compile error."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
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
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)

    # Clean up potential double blank lines left behind
    new_content = re.sub(r'\n{3,}', '\n\n', new_content)

    new_files[file_path] = new_content
    return new_files
