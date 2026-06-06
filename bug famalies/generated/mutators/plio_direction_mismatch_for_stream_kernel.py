import re
import copy

BUG_FAMILY = {
    "family_id": "BF107",
    "bug_type": "plio_direction_mismatch_for_stream_kernel",
    "category": "stream_scalar_interfaces",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_plio",
        "adf::output_plio",
        "plio_32_bits",
        "plio_64_bits",
        "connect<stream>"
    ],
    "mutation_strategy": "Declare an input_plio where an output_plio is needed (or vice versa) for a stream connection to a kernel that uses readincr/writeincr. The PLIO direction conflicts with the kernel port direction in the graph connect statement.",
    "repair_expectation": "Change the PLIO declaration to match the correct direction (input_plio for kernel input stream ports, output_plio for kernel output stream ports).",
    "validation_signal": "WSL Vitis/AIE compile failure with error about incompatible port directions in the ADF graph connection or PLIO direction mismatch.",
    "tags": [
        "direction",
        "input_plio",
        "output_plio",
        "plio",
        "stream",
        "stream_scalar_interfaces"
    ]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for graph files
    if 'graph' in lower:
        return True
    if lower.endswith('.h') or lower.endswith('.hpp') or lower.endswith('.cpp') or lower.endswith('.cc'):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match input_plio or output_plio declarations
    # Matches forms like:
    #   adf::input_plio varname = adf::input_plio::create(...)
    #   input_plio varname = input_plio::create(...)
    #   adf::input_plio varname;
    #   Also member declarations in class bodies
    plio_decl_pattern = re.compile(
        r'((?:adf::)?(input_plio|output_plio))'
        r'(\s+\w+\s*(?:=\s*(?:adf::)?(?:input_plio|output_plio)::create\s*\([^)]*\))?'
        r'|(?:\s+\w+\s*;))'
    )

    # More general pattern that catches various declaration styles
    plio_general_pattern = re.compile(
        r'((?:adf::)?)(input_plio|output_plio)(\b)'
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Check if file contains any PLIO-related content
        if 'input_plio' not in content and 'output_plio' not in content:
            continue

        # Find all occurrences of input_plio and output_plio
        for match in plio_general_pattern.finditer(content):
            prefix = match.group(1)  # "adf::" or ""
            direction = match.group(2)  # "input_plio" or "output_plio"
            full_match = match.group(0)

            # Determine the replacement direction
            if direction == "input_plio":
                new_direction = "output_plio"
            else:
                new_direction = "input_plio"

            replacement = prefix + new_direction + match.group(3)

            start = match.start()
            end = match.end()

            # Skip if this is inside a comment
            line_start = content.rfind('\n', 0, start) + 1
            line = content[line_start:content.find('\n', start)]
            stripped_before = content[line_start:start].lstrip()
            if stripped_before.startswith('//') or stripped_before.startswith('*'):
                continue

            # Check context: skip if it's part of "::create" on the right side of assignment
            # We want to mutate declarations, not just any mention
            # But we'll include all occurrences as candidates for broader coverage

            description = (
                f"Swap '{direction}' to '{new_direction}' at position {start} in {filepath}. "
                f"This creates a PLIO direction mismatch for a stream kernel connection."
            )

            candidates.append({
                "file_path": filepath,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": description
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = new_files[filepath]

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

    new_files[filepath] = new_content
    return new_files
