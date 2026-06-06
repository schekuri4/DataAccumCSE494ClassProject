import re
import copy

BUG_FAMILY = {
    "family_id": "BF073",
    "bug_type": "plio_create_factory_spelling_error",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_plio::create",
        "adf::output_plio::create"
    ],
    "mutation_strategy": "Introduce a spelling error in the PLIO factory method, such as adf::input_plio::Create (capitalized), adf::input_plio::creat, or adf::input_plio_create (missing ::), causing a compile-time symbol resolution failure.",
    "repair_expectation": "Correct the factory method spelling to adf::input_plio::create or adf::output_plio::create.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about undefined member function or no member named 'Create'/'creat' in the plio class.",
    "tags": ["api_error", "factory", "plio", "plio_ports", "spelling"]
}

# Spelling error variants to cycle through for each match
_SPELLING_VARIANTS = [
    ("::create", "::Create"),      # capitalized
    ("::create", "::creat"),       # truncated
    ("_plio::create", "_plio_create"),  # missing :: replaced with _
]


def _is_graph_file(path):
    """Heuristic: graph headers (.h/.hpp) and graph sources (.cpp/.cc) typically contain 'graph' in name or are C++ files."""
    lower = path.lower()
    # Accept any C++ header or source file as potential graph file
    return lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx'))


def find_mutation_candidates(project_files):
    candidates = []
    # Pattern matches adf::input_plio::create or adf::output_plio::create
    pattern = re.compile(r'adf::(input_plio|output_plio)::create\b')

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        for match in pattern.finditer(content):
            plio_type = match.group(1)  # input_plio or output_plio
            original = match.group(0)   # e.g. adf::input_plio::create
            start = match.start()
            end = match.end()

            # Pick a variant based on occurrence index for determinism
            variant_idx = len(candidates) % len(_SPELLING_VARIANTS)
            search_str, replace_str = _SPELLING_VARIANTS[variant_idx]

            # Apply the spelling variant to the original match
            if search_str in original:
                replacement = original.replace(search_str, replace_str, 1)
            else:
                # Fallback: capitalize create
                replacement = original.replace("::create", "::Create", 1)

            # Description of the mutation
            description = (
                f"Introduce spelling error in PLIO factory method: "
                f"'{original}' -> '{replacement}' causing compile-time symbol resolution failure."
            )

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": description
            })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Create a new copy of project_files
    mutated_files = dict(project_files)

    content = mutated_files[file_path]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        mutated_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        mutated_content = content.replace(original, replacement, 1)

    mutated_files[file_path] = mutated_content
    return mutated_files
