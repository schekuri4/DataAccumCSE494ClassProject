import re
import copy

BUG_FAMILY = {
    "family_id": "BF053",
    "bug_type": "plio_direction_inversion",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_plio",
        "adf::output_plio",
        "input_plio::create",
        "output_plio::create"
    ],
    "mutation_strategy": "Change an input_plio declaration to output_plio or vice versa while keeping the same connect wiring, so the PLIO direction conflicts with how it is connected in the graph.",
    "repair_expectation": "Restore the correct PLIO direction (input_plio for data sources feeding into the graph, output_plio for data sinks receiving from the graph).",
    "validation_signal": "WSL Vitis/AIE compile failure with error about incompatible port directions or PLIO direction mismatch in connection.",
    "tags": ["direction", "graph_connections", "input_plio", "output_plio", "plio"]
}


def _is_graph_header(file_path: str) -> bool:
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    if not (lower.endswith('.h') or lower.endswith('.hpp') or lower.endswith('.hxx')):
        return False
    # Broad heuristic: any header could be a graph header
    return True


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    # Patterns for declarations and create calls
    patterns = [
        # adf::input_plio or adf::output_plio (declaration type)
        (re.compile(r'\badf::(input_plio|output_plio)\b'), 'adf_qualified'),
        # input_plio::create or output_plio::create
        (re.compile(r'\b(input_plio|output_plio)::create\b'), 'create_call'),
        # Bare input_plio or output_plio used as type (not preceded by adf:: and not followed by ::create necessarily)
        (re.compile(r'(?<!\w)(?<!adf::)(input_plio|output_plio)(?=\s+\w|\s*<)'), 'bare_type'),
    ]

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Check if file likely contains PLIO-related content
        if 'input_plio' not in content and 'output_plio' not in content:
            continue

        # Pattern 1: adf::input_plio or adf::output_plio
        for match in re.finditer(r'\badf::(input_plio|output_plio)\b', content):
            original_direction = match.group(1)
            new_direction = 'output_plio' if original_direction == 'input_plio' else 'input_plio'
            original_text = match.group(0)
            replacement_text = f'adf::{new_direction}'

            candidates.append({
                "file_path": file_path,
                "bug_type": "plio_direction_inversion",
                "category": "graph_connections",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": f"Inverted PLIO direction from '{original_text}' to '{replacement_text}', causing direction mismatch in graph connections."
            })

        # Pattern 2: input_plio::create or output_plio::create
        for match in re.finditer(r'\b(input_plio|output_plio)(::create)\b', content):
            # Skip if preceded by "adf::" (already caught above in a different form)
            prefix_start = max(0, match.start() - 5)
            prefix = content[prefix_start:match.start()]
            if prefix.endswith('adf::'):
                continue

            original_direction = match.group(1)
            new_direction = 'output_plio' if original_direction == 'input_plio' else 'input_plio'
            original_text = match.group(0)
            replacement_text = f'{new_direction}::create'

            candidates.append({
                "file_path": file_path,
                "bug_type": "plio_direction_inversion",
                "category": "graph_connections",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": f"Inverted PLIO direction from '{original_text}' to '{replacement_text}', causing direction mismatch in graph connections."
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
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
