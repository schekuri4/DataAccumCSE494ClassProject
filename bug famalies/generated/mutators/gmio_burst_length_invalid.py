import re
import copy

BUG_FAMILY = {
    "family_id": "BF083",
    "bug_type": "gmio_burst_length_invalid",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["gmio::create", "adf::GMIO::create", "burst_length"],
    "mutation_strategy": "Change the burst_length parameter in gmio::create to a value that is not a power of 2 or exceeds the maximum allowed (e.g., set burst_length to 100 or 0 instead of valid values like 64, 128, 256).",
    "repair_expectation": "Set burst_length to a valid power-of-2 value within the supported range (e.g., 64, 128, or 256).",
    "validation_signal": "WSL Vitis/AIE compile failure with an error about invalid burst length parameter for GMIO port.",
    "tags": ["burst_length", "compile_error", "gmio", "gmio_ports", "invalid_parameter"]
}

# Valid burst lengths are powers of 2; we mutate to invalid values
INVALID_BURST_LENGTHS = {
    "32": "100",
    "64": "100",
    "128": "100",
    "256": "100",
    "512": "100",
    "1024": "100",
}
DEFAULT_INVALID = "100"


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files, possibly with 'graph' in name."""
    lower = file_path.lower()
    if lower.endswith(('.h', '.hpp', '.hh', '.hxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match gmio::create or adf::GMIO::create calls with burst_length argument
    # Typical signature: gmio::create("name", burst_length, ...)
    # or adf::GMIO::create("name", burst_length, ...)
    # The burst_length is typically the 2nd argument (after the port name string)
    # We look for patterns like:
    #   gmio::create("portname", 64, ...)
    #   adf::GMIO::create("portname", 128, ...)
    # Also handle cases where burst_length might be 3rd arg or named differently

    # Regex: match gmio::create or adf::GMIO::create with arguments
    pattern = re.compile(
        r'((?:adf::)?[Gg][Mm][Ii][Oo]::create\s*\()'  # group 1: function call opening
        r'([^)]*)'  # group 2: all arguments
        r'(\))',  # group 3: closing paren
        re.DOTALL
    )

    # Pattern to find numeric literals that are valid burst lengths (powers of 2)
    burst_pattern = re.compile(r'\b(32|64|128|256|512|1024)\b')

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        for match in pattern.finditer(content):
            args_str = match.group(2)
            # Find burst_length values in the arguments
            for burst_match in burst_pattern.finditer(args_str):
                original_value = burst_match.group(1)
                replacement_value = INVALID_BURST_LENGTHS.get(original_value, DEFAULT_INVALID)

                # Calculate absolute positions in the file
                args_start = match.start(2)
                abs_start = args_start + burst_match.start()
                abs_end = args_start + burst_match.end()

                # Reconstruct the full original and replacement segments
                original_segment = content[abs_start:abs_end]

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "gmio_burst_length_invalid",
                    "category": "gmio_ports",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_segment,
                    "replacement": replacement_value,
                    "description": (
                        f"Changed burst_length from {original_value} to {replacement_value} "
                        f"(not a power of 2) in gmio::create call, causing invalid burst length error."
                    )
                })

    # Also look for standalone burst_length assignments or template parameters
    burst_assign_pattern = re.compile(
        r'(burst_length\s*[=:]\s*)'  # group 1: prefix
        r'(\b(?:32|64|128|256|512|1024)\b)'  # group 2: the value
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        for match in burst_assign_pattern.finditer(content):
            original_value = match.group(2)
            replacement_value = INVALID_BURST_LENGTHS.get(original_value, DEFAULT_INVALID)

            abs_start = match.start(2)
            abs_end = match.end(2)

            # Avoid duplicates
            already_found = any(
                c["file_path"] == file_path and c["start"] == abs_start
                for c in candidates
            )
            if already_found:
                continue

            candidates.append({
                "file_path": file_path,
                "bug_type": "gmio_burst_length_invalid",
                "category": "gmio_ports",
                "start": abs_start,
                "end": abs_end,
                "original": original_value,
                "replacement": replacement_value,
                "description": (
                    f"Changed burst_length from {original_value} to {replacement_value} "
                    f"(not a power of 2) in burst_length assignment/parameter."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    if file_path not in project_files:
        return dict(project_files)

    content = project_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] != original:
        return dict(project_files)

    new_content = content[:start] + replacement + content[end:]

    # Return a new dict (shallow copy with mutated file)
    new_files = dict(project_files)
    new_files[file_path] = new_content
    return new_files
