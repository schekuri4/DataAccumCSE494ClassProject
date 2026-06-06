import re
import copy

BUG_FAMILY = {
    "family_id": "BF071",
    "bug_type": "plio_direction_input_output_swap",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["adf::input_plio", "adf::output_plio", "adf::PLIO"],
    "mutation_strategy": "Swap the direction of a PLIO declaration: change adf::input_plio to adf::output_plio or vice versa, causing a direction mismatch when the port is connected to a kernel input/output port via adf::connect<>.",
    "repair_expectation": "Restore the correct PLIO direction (input_plio for kernel input connections, output_plio for kernel output connections).",
    "validation_signal": "WSL Vitis/AIE compile failure with error indicating port direction mismatch or incompatible connection between PLIO and kernel port.",
    "tags": ["connect_mismatch", "direction", "input_output_swap", "plio", "plio_ports"],
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for graph files in AIE projects
    if "graph" in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might contain graph definitions
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Patterns to match PLIO declarations
    # Match adf::input_plio or adf::output_plio (with optional template params, variable name, etc.)
    pattern_input = re.compile(
        r'(adf::input_plio)'
    )
    pattern_output = re.compile(
        r'(adf::output_plio)'
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Search for adf::input_plio occurrences
        for match in pattern_input.finditer(content):
            start = match.start(1)
            end = match.end(1)
            original = match.group(1)
            replacement = "adf::output_plio"
            candidates.append({
                "file_path": filepath,
                "bug_type": "plio_direction_input_output_swap",
                "category": "plio_ports",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": f"Swap adf::input_plio to adf::output_plio at offset {start} in {filepath}, causing direction mismatch.",
            })

        # Search for adf::output_plio occurrences
        for match in pattern_output.finditer(content):
            start = match.start(1)
            end = match.end(1)
            original = match.group(1)
            replacement = "adf::input_plio"
            candidates.append({
                "file_path": filepath,
                "bug_type": "plio_direction_input_output_swap",
                "category": "plio_ports",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": f"Swap adf::output_plio to adf::input_plio at offset {start} in {filepath}, causing direction mismatch.",
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = project_files[filepath]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    else:
        new_content = content[:start] + candidate["replacement"] + content[end:]

    new_files[filepath] = new_content
    return new_files
