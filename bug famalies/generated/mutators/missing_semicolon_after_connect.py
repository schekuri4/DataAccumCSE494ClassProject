BUG_FAMILY = {
    "family_id": "BF055",
    "bug_type": "missing_semicolon_after_connect",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<window<",
        "connect<stream>",
        "adf::connect<",
        ");"
    ],
    "mutation_strategy": "Remove the semicolon at the end of a connect<>() statement in the graph constructor. This causes a parse error that cascades into subsequent declarations.",
    "repair_expectation": "Add the missing semicolon after the closing parenthesis of the connect statement.",
    "validation_signal": "WSL Vitis/AIE compile failure with syntax error or unexpected token on the line following the connect statement.",
    "tags": ["connect", "graph_connections", "parse_error", "semicolon", "syntax"]
}

import re


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files with 'graph' in name or content."""
    lower = file_path.lower()
    return lower.endswith('.h') or lower.endswith('.hpp')


def find_mutation_candidates(project_files):
    candidates = []
    # Pattern matches connect<...>(...); statements, capturing the semicolon position
    # We look for lines containing connect< ... > ... ( ... ) ;
    connect_pattern = re.compile(
        r'((?:adf::)?connect\s*<[^>]*(?:>[^>]*)*>\s*\([^)]*\))\s*(;)',
        re.DOTALL
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            # Also check if file content looks like a graph header
            if 'connect<' not in content and 'adf::connect<' not in content:
                continue
            if not (file_path.endswith('.h') or file_path.endswith('.hpp')):
                continue

        # Find all connect statements with semicolons
        for match in connect_pattern.finditer(content):
            full_match_start = match.start()
            semicolon_start = match.start(2)
            semicolon_end = match.end(2)
            original = match.group(0)
            replacement = match.group(1)  # everything except the semicolon

            # Verify this matches one of our target patterns
            statement = match.group(1)
            is_target = False
            for target in ["connect<window<", "connect<stream>", "adf::connect<"]:
                if target in statement:
                    is_target = True
                    break
            if not is_target:
                # Still a connect statement but doesn't match specific targets
                if "connect<" in statement:
                    is_target = True
            if not is_target:
                continue

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": semicolon_start,
                "end": semicolon_end,
                "original": ";",
                "replacement": "",
                "description": (
                    f"Remove semicolon after connect<>() statement at offset "
                    f"{semicolon_start} in '{file_path}', causing a parse error."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    content = project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        raise ValueError(
            f"Expected '{original}' at position {start}:{end}, "
            f"but found '{content[start:end]}'"
        )

    mutated_content = content[:start] + candidate["replacement"] + content[end:]

    # Return a new dict (do not mutate in place)
    new_project_files = dict(project_files)
    new_project_files[file_path] = mutated_content
    return new_project_files
