import re
import copy

BUG_FAMILY = {
    "family_id": "BF155",
    "bug_type": "missing_semicolon_after_constraint",
    "category": "graph_runtime_constraints",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "runtime<ratio>",
        "repetition_count",
        "fifo_depth",
        "location<kernel>",
        "single_buffer"
    ],
    "mutation_strategy": "Remove the semicolon at the end of a graph constraint statement such as runtime<ratio>(k1) = 0.9 or fifo_depth(net0) = 128, causing a C++ parse error that manifests as an unexpected token or missing semicolon diagnostic.",
    "repair_expectation": "Add the missing semicolon at the end of the constraint statement.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'expected ;' or syntax error at the line following the constraint.",
    "tags": [
        "compile_time",
        "graph_constraint",
        "graph_runtime_constraints",
        "missing_semicolon",
        "syntax"
    ]
}

# Pattern to match constraint statements ending with a semicolon.
# Matches lines containing any of the match_targets followed by content and a semicolon.
_CONSTRAINT_PATTERN = re.compile(
    r'^([ \t]*'
    r'(?:runtime\s*<\s*ratio\s*>|repetition_count|fifo_depth|location\s*<\s*kernel\s*>|single_buffer)'
    r'\s*\(.*?\)'          # the argument in parentheses
    r'(?:\s*=\s*[^;]+?)?'  # optional assignment (e.g., = 0.9, = 128)
    r')\s*(;)\s*$',        # trailing semicolon
    re.MULTILINE
)


def _is_graph_file(filepath):
    """Heuristic: graph headers (.h/.hpp) or graph source (.cpp/.cc) files
    that likely contain graph definitions."""
    lower = filepath.lower()
    # Accept files that look like graph headers or sources
    if 'graph' in lower:
        return True
    # Also accept any .h/.hpp/.cpp/.cc file (AIE projects are small)
    if any(lower.endswith(ext) for ext in ('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        for match in _CONSTRAINT_PATTERN.finditer(content):
            # The full match includes the statement and semicolon
            full_start = match.start()
            full_end = match.end()
            original_line = match.group(0)
            # The semicolon is group(2)
            semicolon_start = match.start(2)
            semicolon_end = match.end(2)
            # Replacement: the line without the semicolon
            # We replace just the semicolon with empty string
            # But we report the full line for context
            replacement_line = original_line[:semicolon_start - full_start] + original_line[semicolon_end - full_start:]
            # Determine which constraint keyword matched
            constraint_keyword = ""
            for target in BUG_FAMILY["match_targets"]:
                # Normalize for regex check
                target_pattern = target.replace("<", r"\s*<\s*").replace(">", r"\s*>")
                if re.search(target_pattern, match.group(1)):
                    constraint_keyword = target
                    break

            candidates.append({
                "file_path": filepath,
                "bug_type": "missing_semicolon_after_constraint",
                "category": "graph_runtime_constraints",
                "start": full_start,
                "end": full_end,
                "original": original_line,
                "replacement": replacement_line,
                "description": (
                    f"Remove semicolon after '{constraint_keyword}' constraint statement "
                    f"in '{filepath}', causing a C++ parse error."
                )
            })
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
