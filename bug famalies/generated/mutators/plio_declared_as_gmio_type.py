import re
import copy

BUG_FAMILY = {
    "family_id": "BF077",
    "bug_type": "plio_declared_as_gmio_type",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_plio",
        "adf::output_plio",
        "adf::input_gmio",
        "adf::output_gmio"
    ],
    "mutation_strategy": "Replace an adf::input_plio or adf::output_plio declaration with adf::input_gmio or adf::output_gmio (or vice versa) while keeping the rest of the connection and creation code unchanged, causing a port type mismatch in the graph connections.",
    "repair_expectation": "Restore the correct port type (PLIO vs GMIO) matching the intended interface and the corresponding create/connect calls.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about type mismatch, incompatible port types in connect, or wrong factory method for the declared type.",
    "tags": [
        "declaration_mismatch",
        "gmio",
        "plio",
        "plio_ports",
        "port_type"
    ]
}

# Mapping from original type to its mutated replacement
_SWAP_MAP = {
    "adf::input_plio": "adf::input_gmio",
    "adf::output_plio": "adf::output_gmio",
    "adf::input_gmio": "adf::input_plio",
    "adf::output_gmio": "adf::output_plio",
}

# Also handle without adf:: prefix
_SWAP_MAP_NO_NS = {
    "input_plio": "input_gmio",
    "output_plio": "output_gmio",
    "input_gmio": "input_plio",
    "output_gmio": "output_plio",
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for graph files
    if 'graph' in lower:
        return True
    # Header or source files that might contain graph definitions
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match declarations like: adf::input_plio varname; or adf::output_plio varname;
    # Also matches without semicolons (e.g., in member declarations, assignments, etc.)
    # We look for the type token specifically
    pattern_with_ns = re.compile(
        r'\b(adf\s*::\s*(?:input_plio|output_plio|input_gmio|output_gmio))\b'
    )
    pattern_no_ns = re.compile(
        r'\b((?:input_plio|output_plio|input_gmio|output_gmio))\b'
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Search with adf:: namespace prefix first
        for match in pattern_with_ns.finditer(content):
            original_text = match.group(1)
            # Normalize whitespace in the matched text for lookup
            normalized = re.sub(r'\s+', '', original_text)

            if normalized in _SWAP_MAP:
                replacement = _SWAP_MAP[normalized]
                start = match.start(1)
                end = match.end(1)

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "plio_declared_as_gmio_type",
                    "category": "plio_ports",
                    "start": start,
                    "end": end,
                    "original": original_text,
                    "replacement": replacement,
                    "description": (
                        f"Replace '{original_text}' with '{replacement}' "
                        f"in {filepath} to introduce a port type mismatch."
                    )
                })

        # Also check for usages without adf:: prefix, but only if we haven't
        # already captured them as part of adf:: matches
        # We need to avoid double-matching positions already found
        already_matched_positions = set()
        for match in pattern_with_ns.finditer(content):
            for pos in range(match.start(), match.end()):
                already_matched_positions.add(pos)

        for match in pattern_no_ns.finditer(content):
            if match.start() in already_matched_positions:
                continue
            # Make sure this isn't preceded by "adf::" (with possible whitespace)
            prefix_check = content[max(0, match.start() - 10):match.start()]
            if re.search(r'adf\s*::\s*$', prefix_check):
                continue

            original_text = match.group(1)
            if original_text in _SWAP_MAP_NO_NS:
                replacement = _SWAP_MAP_NO_NS[original_text]
                start = match.start(1)
                end = match.end(1)

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "plio_declared_as_gmio_type",
                    "category": "plio_ports",
                    "start": start,
                    "end": end,
                    "original": original_text,
                    "replacement": replacement,
                    "description": (
                        f"Replace '{original_text}' with '{replacement}' "
                        f"in {filepath} to introduce a port type mismatch."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    if filepath not in new_files:
        return new_files

    content = new_files[filepath]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
