import re
import copy

BUG_FAMILY = {
    "family_id": "BF028",
    "bug_type": "plio_direction_mismatch_in_graph",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "PLIO::create(",
        "input_plio",
        "output_plio",
        "adf::input_plio",
        "adf::output_plio"
    ],
    "mutation_strategy": "Swap the PLIO direction declarations—declare an input_plio where output_plio is needed or vice versa.",
    "repair_expectation": "Correct the PLIO direction to match its role in the graph: input_plio for data sources, output_plio for data sinks.",
    "validation_signal": "WSL Vitis/AIE compile failure with port direction mismatch or illegal connection between incompatible port directions.",
    "tags": ["direction", "graph_kernel_binding", "graph_port", "input_output", "plio"]
}


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files with 'graph' in name or path."""
    lower = file_path.lower()
    if not (lower.endswith('.h') or lower.endswith('.hpp') or lower.endswith('.hxx')):
        return False
    return True


def find_mutation_candidates(project_files):
    candidates = []

    # Patterns to match PLIO declarations
    # Match: input_plio, output_plio, adf::input_plio, adf::output_plio
    plio_decl_pattern = re.compile(
        r'((?:adf::)?)(input_plio|output_plio)(\s+\w+)'
    )

    # Also match PLIO::create with "input" or "output" as argument
    plio_create_pattern = re.compile(
        r'(PLIO::create\s*\(\s*"[^"]*"\s*,\s*)(adf::plio_dir::in|adf::plio_dir::out|plio_dir::in|plio_dir::out)'
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Check if file has any PLIO-related content
        has_plio = any(mt in content for mt in BUG_FAMILY["match_targets"])
        if not has_plio:
            continue

        # Find input_plio / output_plio declarations
        for match in plio_decl_pattern.finditer(content):
            prefix = match.group(1)  # "adf::" or ""
            direction = match.group(2)  # "input_plio" or "output_plio"
            suffix = match.group(3)

            if direction == "input_plio":
                new_direction = "output_plio"
            else:
                new_direction = "input_plio"

            original = match.group(0)
            replacement = prefix + new_direction + suffix

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Swap PLIO direction from '{direction}' to '{new_direction}' for variable{suffix.strip()}, causing direction mismatch in graph connections."
            })

        # Find PLIO::create with direction argument
        for match in plio_create_pattern.finditer(content):
            prefix_part = match.group(1)
            dir_arg = match.group(2)

            if "in" in dir_arg and "out" not in dir_arg:
                new_dir = dir_arg.replace("in", "out")
            elif "out" in dir_arg:
                new_dir = dir_arg.replace("out", "in")
            else:
                continue

            original = match.group(0)
            replacement = prefix_part + new_dir

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Swap PLIO::create direction from '{dir_arg}' to '{new_dir}', causing direction mismatch."
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
