import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF075",
    "bug_type": "plio_connect_template_type_mismatch",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["adf::connect<", "window<", "stream"],
    "mutation_strategy": "Change the connect<> template parameter when connecting a PLIO port to use an incompatible type, such as using connect<window<256>> where connect<stream> is required for PLIO, or using connect<parameter> instead of connect<stream>.",
    "repair_expectation": "Use the correct connection type for PLIO ports, typically adf::connect<> (stream-based) without window semantics, or the appropriate stream template.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about incompatible connection types or no viable overload for connect between PLIO and kernel port.",
    "tags": ["connect", "plio", "plio_ports", "template", "type_mismatch"]
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


def _line_offsets(lines):
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    return offsets


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match adf::connect< ... >( ... ) or connect< ... >( ... )
    # We look for connect statements that involve PLIO ports
    # Match connect<> with empty or stream template near PLIO references
    connect_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*)((?:adf::)?stream|pktstream|)(\s*>\s*\()'
    )

    # Also match connect<stream> explicitly
    connect_stream_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*)((?:adf::)?stream|pktstream)(\s*>\s*\()'
    )

    # Match empty connect<> which is stream-based for PLIO
    connect_empty_pattern = re.compile(
        r'((?:adf::)?connect\s*<)(\s*)(>\s*\()'
    )

    # Match connect with window template (to potentially find PLIO misuse candidates)
    connect_window_pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*)((?:adf::)?window\s*<\s*\d+\s*>)(\s*>\s*\()'
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Check if file has PLIO references (strong indicator of graph with PLIO)
        has_plio = bool(re.search(r'(?:adf::)?(?:PLIO|plio|input_plio|output_plio)', content))

        lines = content.split('\n')
        offsets = _line_offsets(lines)

        for line_idx, line in enumerate(lines):
            # Strategy 1: Find connect<> (empty/stream) near PLIO and mutate to window
            for m in connect_empty_pattern.finditer(line):
                # Empty connect<> is stream-based, used for PLIO
                # Check if this line or nearby lines reference PLIO
                context = '\n'.join(lines[max(0, line_idx - 5):min(len(lines), line_idx + 5)])
                if has_plio or re.search(r'(?:plio|PLIO|input_plio|output_plio)', context):
                    original = m.group(0)
                    # Replace empty connect<> with connect<window<256>>
                    replacement = m.group(1) + 'window<256>' + m.group(3)
                    start = m.start()
                    end = m.end()
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "plio_connect_template_type_mismatch",
                        "category": "plio_ports",
                        "start": offsets[line_idx] + start,
                        "end": offsets[line_idx] + end,
                        "original": original,
                        "replacement": replacement,
                        "description": "Changed PLIO connect<> (stream) to connect<window<256>> causing type mismatch"
                    })

            # Strategy 2: Find connect<stream> and mutate to connect<window<256>>
            for m in connect_stream_pattern.finditer(line):
                context = '\n'.join(lines[max(0, line_idx - 5):min(len(lines), line_idx + 5)])
                if has_plio or re.search(r'(?:plio|PLIO|input_plio|output_plio)', context):
                    original = m.group(0)
                    replacement = m.group(1) + 'window<256>' + m.group(3)
                    start = m.start()
                    end = m.end()
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "plio_connect_template_type_mismatch",
                        "category": "plio_ports",
                        "start": offsets[line_idx] + start,
                        "end": offsets[line_idx] + end,
                        "original": original,
                        "replacement": replacement,
                        "description": "Changed PLIO connect<stream> to connect<window<256>> causing type mismatch"
                    })

                    # Also offer parameter mutation
                    replacement2 = m.group(1) + 'parameter' + m.group(3)
                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "plio_connect_template_type_mismatch",
                        "category": "plio_ports",
                        "start": offsets[line_idx] + start,
                        "end": offsets[line_idx] + end,
                        "original": original,
                        "replacement": replacement2,
                        "description": "Changed PLIO connect<stream> to connect<parameter> causing type mismatch"
                    })

        # Strategy 3: Broader pattern - any adf::connect< with content that looks stream-related
        # Pattern for adf::connect<...> where ... could be various things
        broad_pattern = re.compile(
            r'((?:adf::)?connect\s*<\s*)(.*?)(\s*>\s*\()'
        )

        for line_idx, line in enumerate(lines):
            for m in broad_pattern.finditer(line):
                template_content = m.group(2).strip()
                # Skip if we already handled this above
                if template_content == '' or template_content in ('stream', 'adf::stream', 'pktstream'):
                    continue
                # Skip if already a window type (could be legitimate kernel-to-kernel)
                if 'window' in template_content:
                    # If PLIO context, suggest changing to parameter (wrong either way)
                    context = '\n'.join(lines[max(0, line_idx - 5):min(len(lines), line_idx + 5)])
                    if has_plio or re.search(r'(?:plio|PLIO|input_plio|output_plio)', context):
                        # This might already be a bug or we can make it worse
                        pass
                    continue

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    filepath = candidate["file_path"]

    if filepath not in new_files:
        return new_files

    content = new_files[filepath]
    original = candidate["original"]
    replacement = candidate["replacement"]

    start = int(candidate["start"])
    end = int(candidate["end"])
    if content[start:end] == original:
        new_files[filepath] = content[:start] + replacement + content[end:]
    else:
        new_files[filepath] = content.replace(original, replacement, 1)

    return new_files
