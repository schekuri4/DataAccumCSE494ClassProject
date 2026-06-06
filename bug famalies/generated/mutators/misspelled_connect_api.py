BUG_FAMILY = {
    "family_id": "BF454",
    "bug_type": "misspelled_connect_api",
    "category": "api_spelling_regressions",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::connect<",
        "connect<window<",
        "connect<stream"
    ],
    "mutation_strategy": "Misspell the graph connect API in a kernel graph constructor so the compiler cannot resolve the connection call. Typical forms are connect -> conect or connect -> connnect while keeping the rest of the template arguments and port references unchanged.",
    "repair_expectation": "Restore the exact connect spelling, usually adf::connect<...>(...).",
    "validation_signal": "WSL Vitis/AIE compile failure with an undeclared identifier or not-a-member error for the misspelled connect API.",
    "tags": [
        "adf",
        "api_spelling_regressions",
        "compile_time",
        "connect",
        "graph",
        "spelling"
    ]
}

import re
import copy

# Misspelling variants for "connect"
_MISSPELLINGS = ["conect", "connnect", "connecct", "connetc"]

# Pattern matches the three match_targets forms:
# - adf::connect<
# - connect<window<
# - connect<stream
# We capture everything up to and including "connect" so we can replace just that word.
_CONNECT_PATTERN = re.compile(
    r'((?:adf\s*::\s*)?)\bconnect\s*(<)'
)


def _is_graph_file(file_path):
    """Heuristic: graph headers (.h/.hpp) or graph sources (.cpp/.cc) that likely contain graph code."""
    lower = file_path.lower()
    # Accept common graph file patterns
    if 'graph' in lower:
        return True
    # Accept any header or source that might be a graph file
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        for match in _CONNECT_PATTERN.finditer(content):
            prefix = match.group(1)  # e.g. "adf::" or ""
            # The full matched text is prefix + "connect" + "<"
            # We want to find the "connect" portion within the match
            full_match = match.group(0)
            start = match.start()
            end = match.end()

            # Determine the original text we'll replace
            original = full_match

            # Choose a misspelling - use first one for determinism
            misspelled_word = _MISSPELLINGS[0]  # "conect"

            # Build replacement: prefix + misspelled + "<"
            replacement = prefix + misspelled_word + "<"

            # Verify this is actually a connect API call (not inside a comment or string)
            # Simple heuristic: check if line starts with // or is inside /* */
            line_start = content.rfind('\n', 0, start) + 1
            line_text = content[line_start:start].lstrip()
            if line_text.startswith('//') or line_text.startswith('*'):
                continue

            description = (
                f"Misspell 'connect' as '{misspelled_word}' in "
                f"'{original.strip()}' at offset {start} in {file_path}"
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
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected location
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    else:
        new_content = content[:start] + candidate["replacement"] + content[end:]

    new_files[file_path] = new_content
    return new_files
