import re
import copy

BUG_FAMILY = {
    "family_id": "BF452",
    "bug_type": "misspelled_adf_type",
    "category": "function_and_member_naming",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::graph",
        "adf::kernel",
        "adf::connect",
        "adf::port",
        "adf::GMIO",
        "adf::PLIO"
    ],
    "mutation_strategy": "Introduce a spelling mistake into a core ADF type or API symbol used in the graph definition, such as changing adf::graph to adf::grahp, adf::kernel to adf::kernal, or adf::connect to adf::conect. This targets a common class of compile failures where the graph compiles only when the exact ADF symbol spelling is correct.",
    "repair_expectation": "Restore the exact ADF type or API spelling in the graph file so the compiler can resolve the symbol.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared type or missing member errors for the misspelled ADF symbol.",
    "tags": [
        "adf",
        "compile_error",
        "function_and_member_naming",
        "graph",
        "spelling",
        "typo"
    ]
}


def _is_target_file(file_path):
    lower = file_path.lower()
    return lower.endswith(('.cpp', '.cc', '.c', '.cxx', '.h', '.hpp', '.hxx'))


def find_mutation_candidates(project_files):
    candidates = []
    replacements = {
        'adf::graph': 'adf::grahp',
        'adf::kernel': 'adf::kernal',
        'adf::connect': 'adf::conect',
        'adf::port': 'adf::prot',
        'adf::GMIO': 'adf::GMOI',
        'adf::PLIO': 'adf::PLIo'
    }

    for file_path, content in project_files.items():
        if not _is_target_file(file_path):
            continue
        for original, replacement in replacements.items():
            start = content.find(original)
            if start == -1:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": start + len(original),
                "original": original,
                "replacement": replacement,
                "description": f"Misspelled {original} as {replacement} in {file_path}."
            })
            break

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        new_content = content.replace(original, replacement, 1)
    new_files[file_path] = new_content
    return new_files
