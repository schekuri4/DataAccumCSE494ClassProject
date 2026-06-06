import re
import copy

BUG_FAMILY = {
    "family_id": "BF082",
    "bug_type": "gmio_direction_output_as_input",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::output_gmio",
        "gmio::create",
        "adf::direction::out"
    ],
    "mutation_strategy": "Replace an output_gmio declaration with input_gmio (or change direction::out to direction::in) while the graph connects it to a kernel output port, creating a direction conflict.",
    "repair_expectation": "Restore the GMIO port declaration to output_gmio or adf::direction::out to match the kernel output connection.",
    "validation_signal": "WSL Vitis/AIE compile failure reporting direction mismatch or illegal connection from kernel output to input GMIO.",
    "tags": [
        "compile_error",
        "direction",
        "gmio",
        "gmio_ports",
        "output_to_input"
    ]
}


def _is_graph_header(file_path):
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Common patterns for graph headers
    if lower.endswith('.h') or lower.endswith('.hpp'):
        if 'graph' in lower:
            return True
        return True  # Any header could be a graph header
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Pattern 1: adf::output_gmio or output_gmio declarations
        pattern1 = re.compile(r'\boutput_gmio\b')
        for m in pattern1.finditer(content):
            candidates.append({
                "file_path": file_path,
                "bug_type": "gmio_direction_output_as_input",
                "category": "gmio_ports",
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": "input_gmio",
                "description": "Changed output_gmio to input_gmio, creating a direction conflict with kernel output port connection."
            })

        # Pattern 2: adf::direction::out in gmio::create calls
        pattern2 = re.compile(r'adf::direction::out')
        for m in pattern2.finditer(content):
            # Check if this is within a gmio::create context (look at surrounding line)
            line_start = content.rfind('\n', 0, m.start()) + 1
            line_end = content.find('\n', m.end())
            if line_end == -1:
                line_end = len(content)
            line = content[line_start:line_end]
            if 'gmio' in line.lower() or 'create' in line:
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "gmio_direction_output_as_input",
                    "category": "gmio_ports",
                    "start": m.start(),
                    "end": m.end(),
                    "original": m.group(0),
                    "replacement": "adf::direction::in",
                    "description": "Changed adf::direction::out to adf::direction::in in gmio::create, creating a direction conflict with kernel output port connection."
                })

        # Pattern 3: direction::out without adf:: prefix in gmio context
        pattern3 = re.compile(r'(?<!adf::)direction::out')
        for m in pattern3.finditer(content):
            line_start = content.rfind('\n', 0, m.start()) + 1
            line_end = content.find('\n', m.end())
            if line_end == -1:
                line_end = len(content)
            line = content[line_start:line_end]
            if 'gmio' in line.lower() or 'create' in line:
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "gmio_direction_output_as_input",
                    "category": "gmio_ports",
                    "start": m.start(),
                    "end": m.end(),
                    "original": m.group(0),
                    "replacement": "direction::in",
                    "description": "Changed direction::out to direction::in in gmio::create, creating a direction conflict with kernel output port connection."
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: find first occurrence and replace
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
