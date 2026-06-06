import re
import copy

BUG_FAMILY = {
    "family_id": "BF081",
    "bug_type": "gmio_direction_input_as_output",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_gmio",
        "gmio::create",
        "adf::GMIO::create"
    ],
    "mutation_strategy": "Replace an input_gmio declaration with output_gmio (or change the direction template parameter from adf::direction::in to adf::direction::out) while keeping the graph connections that feed data into a kernel input port, causing a direction mismatch at compile time.",
    "repair_expectation": "Restore the GMIO port declaration to input_gmio or adf::direction::in so that the port direction matches the kernel input connection.",
    "validation_signal": "WSL Vitis/AIE compile failure with an error indicating port direction mismatch or incompatible connection between output GMIO and kernel input.",
    "tags": [
        "compile_error",
        "direction",
        "gmio",
        "gmio_ports",
        "input_output_swap"
    ]
}


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files, often containing 'graph' in name."""
    lower = file_path.lower()
    # Accept any header file that could be a graph header
    return lower.endswith('.h') or lower.endswith('.hpp')


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern 1: adf::input_gmio or input_gmio declarations
    pattern_input_gmio = re.compile(
        r'((?:adf::)?input_gmio)\b'
    )

    # Pattern 2: gmio::create or adf::GMIO::create with direction::in
    pattern_create_dir_in = re.compile(
        r'(adf::direction::in)\b'
    )

    # Pattern 3: GMIO::create or gmio::create calls - look for direction template param
    pattern_gmio_create = re.compile(
        r'((?:adf::)?(?:GMIO|gmio)::create\s*<\s*)(adf::direction::in)(\s*[,>])'
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Search for input_gmio type declarations
        for m in pattern_input_gmio.finditer(content):
            original = m.group(1)
            # Determine replacement
            if original == 'adf::input_gmio':
                replacement = 'adf::output_gmio'
            elif original == 'input_gmio':
                replacement = 'output_gmio'
            else:
                continue

            candidates.append({
                "file_path": file_path,
                "bug_type": "gmio_direction_input_as_output",
                "category": "gmio_ports",
                "start": m.start(1),
                "end": m.end(1),
                "original": original,
                "replacement": replacement,
                "description": f"Replace '{original}' with '{replacement}' to introduce direction mismatch on GMIO port."
            })

        # Search for gmio::create<adf::direction::in, ...> or adf::GMIO::create<adf::direction::in, ...>
        for m in pattern_gmio_create.finditer(content):
            original = m.group(2)
            replacement = 'adf::direction::out'

            candidates.append({
                "file_path": file_path,
                "bug_type": "gmio_direction_input_as_output",
                "category": "gmio_ports",
                "start": m.start(2),
                "end": m.end(2),
                "original": original,
                "replacement": replacement,
                "description": f"Replace '{original}' with '{replacement}' in GMIO::create template parameter to introduce direction mismatch."
            })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    content = project_files[file_path]

    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: should not happen with deterministic candidates
        return project_files

    mutated_content = content[:start] + replacement + content[end:]

    # Return a new dict with the mutated file
    new_project_files = dict(project_files)
    new_project_files[file_path] = mutated_content
    return new_project_files
