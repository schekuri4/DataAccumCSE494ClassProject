import re


BUG_FAMILY = {
    "family_id": "BF_MANUAL_GRAPH_001",
    "bug_type": "graph_dimensions_expression_truncated",
    "category": "graph_runtime_constraints",
    "target_files": ["graph source", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["dimensions(", "adf::dimensions(", "= {"],
    "mutation_strategy": (
        "Replace a nontrivial graph dimensions(endpoint) expression with 1, "
        "making the declared buffer/window size inconsistent with the kernel interface."
    ),
    "repair_expectation": "Restore the full dimensions expression for the graph endpoint.",
    "validation_signal": "WSL Vitis/AIE compile failure with graph dimension, window size, or buffer size mismatch.",
    "tags": ["dimensions", "graph", "runtime_constraint", "single_span"],
}


_DIMENSIONS_PATTERN = re.compile(
    r'((?:adf::)?dimensions\s*\([^)]+\)\s*=\s*\{\s*)([^};\n]+)(\s*\})'
)


def _is_graph_file(path):
    return path.lower().endswith((".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"))


def find_mutation_candidates(project_files):
    candidates = []
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        if "dimensions" not in content:
            continue
        for match in _DIMENSIONS_PATTERN.finditer(content):
            original = match.group(2)
            if original.strip() in {"0", "1"}:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(2),
                "end": match.end(2),
                "original": original,
                "replacement": "1",
                "description": (
                    f"Truncated graph dimensions expression '{original.strip()}' to 1, "
                    f"creating a graph/kernel size mismatch."
                ),
            })
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
        new_files[file_path] = content[:start] + replacement + content[end:]
    else:
        new_files[file_path] = content.replace(original, replacement, 1)
    return new_files
