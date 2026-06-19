import re
import copy

BUG_FAMILY = {
    "family_id": "BF085",
    "bug_type": "gmio_connect_type_mismatch",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::connect<>",
        "adf::connect<adf::stream>",
        "adf::connect<adf::window>",
        "gmio"
    ],
    "mutation_strategy": "Change the connect template specialization between a GMIO port and a kernel port to use adf::connect<adf::window<N>> instead of the required adf::connect<> (or vice versa), creating a type mismatch since GMIO only supports stream connections.",
    "repair_expectation": "Use the correct adf::connect<> (stream-based) template for GMIO-to-kernel connections.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating incompatible connection type between GMIO port and kernel port.",
    "tags": [
        "compile_error",
        "connect",
        "gmio",
        "gmio_ports",
        "stream_window",
        "type_mismatch"
    ]
}


def _is_graph_header(filepath):
    """Heuristic: graph headers are .h or .hpp files likely containing graph definitions."""
    lower = filepath.lower()
    return lower.endswith('.h') or lower.endswith('.hpp')


def _file_contains_gmio(content):
    """Check if file references gmio (indicating it's a graph header with GMIO ports)."""
    return 'gmio' in content.lower() or 'GMIO' in content


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_header(filepath):
            continue
        if not _file_contains_gmio(content):
            continue

        # Strategy 1: Find adf::connect<> or adf::connect<adf::stream> near gmio references
        # and replace with adf::connect<adf::window<N>>
        # Pattern matches connect statements that involve gmio ports
        # Look for lines with connect that reference a gmio member

        # First, find GMIO port member names
        gmio_pattern = re.compile(r'\b(?:adf::)?(?:input_gmio|output_gmio|GMIO)\s+(\w+)')
        gmio_members = set()
        for m in gmio_pattern.finditer(content):
            gmio_members.add(m.group(1))

        # Also look for members declared as gmio type
        gmio_pattern2 = re.compile(r'\b(?:adf::)?gmio\s+(\w+)')
        for m in gmio_pattern2.finditer(content):
            gmio_members.add(m.group(1))

        # Pattern for connect<> or connect<adf::stream> statements
        # adf::connect<> name(port1, port2) or adf::connect<>(port1, port2)
        connect_pattern = re.compile(
            r'((?:adf::)?connect\s*<\s*(?:(?:adf::)?stream)?\s*>)\s*'
            r'(\w+)?\s*\(\s*([^)]+)\)'
        )

        for m in connect_pattern.finditer(content):
            full_connect = m.group(1)
            args = m.group(3)

            # Check if any gmio member is referenced in the connect arguments
            involves_gmio = False
            for gm in gmio_members:
                if gm in args:
                    involves_gmio = True
                    break

            # Also check for .out[0], .in[0] patterns with gmio-like names
            if not involves_gmio:
                # Check if 'gm' or 'gmio' appears in args
                if re.search(r'gm|GMIO|gmio', args, re.IGNORECASE):
                    involves_gmio = True

            if not involves_gmio and gmio_members:
                # Broader check: any gmio member with dot notation
                for gm in gmio_members:
                    if gm in content[max(0, m.start()-100):m.end()+100]:
                        involves_gmio = True
                        break

            if not involves_gmio:
                continue

            # Create mutation: replace connect<> or connect<adf::stream> with connect<adf::window<128>>
            start = m.start(1)
            end = m.end(1)
            original = full_connect
            replacement = "connect<window<128>>" if not original.startswith("adf::") else "adf::connect<adf::window<128>>"

            candidates.append({
                "file_path": filepath,
                "bug_type": "gmio_connect_type_mismatch",
                "category": "gmio_ports",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed '{original}' to '{replacement}' for a GMIO connection, "
                    f"creating a type mismatch since GMIO only supports stream connections."
                )
            })

        # Strategy 2: Find adf::connect<adf::window<N>> that involves gmio and change to connect<>
        # (reverse mutation for files that already have the bug pattern inverted)
        window_connect_pattern = re.compile(
            r'((?:adf::)?connect\s*<\s*(?:adf::)?window\s*<\s*\d+\s*>\s*>)\s*'
            r'(\w+)?\s*\(\s*([^)]+)\)'
        )

        for m in window_connect_pattern.finditer(content):
            full_connect = m.group(1)
            args = m.group(3)

            involves_gmio = False
            for gm in gmio_members:
                if gm in args:
                    involves_gmio = True
                    break
            if not involves_gmio:
                if re.search(r'gm|GMIO|gmio', args, re.IGNORECASE):
                    involves_gmio = True

            if not involves_gmio:
                continue

            # This is already a window connect with gmio - not a valid mutation target
            # since it's already buggy. Skip unless we want to do reverse.
            # Actually per strategy we can also go from window to stream to test the reverse.
            # But the bug family wants to introduce the bug, so skip these.

        # Strategy 3: If no gmio members found explicitly, look for connect statements
        # on lines near 'gmio' keyword usage
        if not candidates or not gmio_members:
            # Broader search: any connect<> in a file with gmio
            for m in connect_pattern.finditer(content):
                full_connect = m.group(1)
                start = m.start(1)
                end = m.end(1)
                original = full_connect
                replacement = "connect<window<128>>" if not original.startswith("adf::") else "adf::connect<adf::window<128>>"

                # Avoid duplicates
                already = any(c["start"] == start and c["file_path"] == filepath for c in candidates)
                if already:
                    continue

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "gmio_connect_type_mismatch",
                    "category": "gmio_ports",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Changed '{original}' to '{replacement}' in a graph header with GMIO ports, "
                        f"creating a type mismatch since GMIO only supports stream connections."
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

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[filepath] = new_content
    return new_files
