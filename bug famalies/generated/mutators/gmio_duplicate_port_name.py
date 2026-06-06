import re
import copy
from typing import Any


BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF090",
    "bug_type": "gmio_duplicate_port_name",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["gmio::create", "adf::GMIO::create"],
    "mutation_strategy": "Create two GMIO ports using gmio::create with the same string name identifier (e.g., both named \"gmio_in\"), which causes a compile-time or elaboration-time name collision error in the ADF graph compiler.",
    "repair_expectation": "Give each GMIO port a unique string name in its factory create call.",
    "validation_signal": "WSL Vitis/AIE compile failure reporting duplicate port name or name collision in the platform graph.",
    "tags": [
        "compile_error",
        "duplicate_name",
        "factory",
        "gmio",
        "gmio_ports",
        "name_collision"
    ]
}


def _is_graph_header(path: str) -> bool:
    """Heuristic: graph headers are .h or .hpp files likely containing graph definitions."""
    lower = path.lower()
    return lower.endswith('.h') or lower.endswith('.hpp')


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match gmio::create or adf::GMIO::create calls with a string name argument
    # Captures the full call including the string name
    pattern = re.compile(
        r'((?:adf::)?[Gg][Mm][Ii][Oo]::create\s*\(\s*"([^"]*)")'
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Find all GMIO::create calls in this file
        matches = list(pattern.finditer(content))
        if len(matches) < 2:
            # Need at least 2 GMIO create calls to create a duplicate
            continue

        # For each pair where the names are different, we can mutate the second
        # to have the same name as the first, creating a collision
        first_match = matches[0]
        first_name = first_match.group(2)

        for i in range(1, len(matches)):
            other_match = matches[i]
            other_name = other_match.group(2)

            if other_name == first_name:
                # Already duplicated, skip
                continue

            # We'll mutate the second call's name string to match the first
            # Find the position of the string literal in the second match
            # The full match group(1) contains everything up to and including the closing quote of the name
            # We need to replace just the name string portion
            # Find the quoted string within the match
            name_pattern = re.compile(r'"' + re.escape(other_name) + r'"')
            name_in_call = name_pattern.search(content, other_match.start())
            if name_in_call is None:
                continue

            original_str = f'"{other_name}"'
            replacement_str = f'"{first_name}"'

            candidate = {
                "file_path": file_path,
                "bug_type": "gmio_duplicate_port_name",
                "category": "gmio_ports",
                "start": name_in_call.start(),
                "end": name_in_call.end(),
                "original": original_str,
                "replacement": replacement_str,
                "description": (
                    f"Duplicate GMIO port name: changed \"{other_name}\" to "
                    f"\"{first_name}\" in second gmio::create call, causing a "
                    f"name collision."
                )
            }
            candidates.append(candidate)
            # Only generate one candidate per file pair to keep things manageable
            break

    # If we didn't find files with multiple GMIO creates but found files with at least one,
    # we can duplicate the line to create a collision
    if not candidates:
        for file_path, content in project_files.items():
            if not _is_graph_header(file_path):
                continue

            matches = list(pattern.finditer(content))
            if len(matches) == 1:
                # We have exactly one GMIO::create - duplicate the entire statement line
                match = matches[0]
                gmio_name = match.group(2)

                # Find the full line containing this match
                line_start = content.rfind('\n', 0, match.start()) + 1
                line_end = content.find('\n', match.end())
                if line_end == -1:
                    line_end = len(content)

                original_line = content[line_start:line_end]

                # Create a duplicate line with a different variable name but same port name
                # We'll insert a duplicate line right after
                # Change the variable name in the duplicate to avoid C++ redefinition
                # but keep the GMIO string name the same
                dup_line = original_line
                # Try to find a variable assignment pattern like: var = gmio::create(...)
                var_pattern = re.compile(r'(\b\w+)\s*=\s*(?:adf::)?[Gg][Mm][Ii][Oo]::create')
                var_match = var_pattern.search(original_line)
                if var_match:
                    old_var = var_match.group(1)
                    new_var = old_var + "_dup"
                    dup_line = original_line[:var_match.start(1)] + new_var + original_line[var_match.end(1):]
                else:
                    # Maybe it's a member declaration; just duplicate as-is with comment
                    dup_line = original_line + "  // duplicated"

                replacement = original_line + '\n' + dup_line

                candidate = {
                    "file_path": file_path,
                    "bug_type": "gmio_duplicate_port_name",
                    "category": "gmio_ports",
                    "start": line_start,
                    "end": line_end,
                    "original": original_line,
                    "replacement": replacement,
                    "description": (
                        f"Duplicated GMIO port creation with same name \"{gmio_name}\" "
                        f"to cause a name collision error."
                    )
                }
                candidates.append(candidate)
                break

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual = content[start:end]
    if actual == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
