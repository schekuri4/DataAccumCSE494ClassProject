import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF074",
    "bug_type": "plio_filename_path_mismatch",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "reference_missing_file",
    "match_targets": [
        'input_plio::create("',
        'output_plio::create("',
        '.txt"',
        '.dat"',
    ],
    "mutation_strategy": "Change the data file path string in a PLIO create call to reference a nonexistent file (e.g., change 'data/input.txt' to 'data/inputt.txt' or 'dat/input.txt'), or use an incorrect relative path that will fail during compilation/linking of the data flow graph.",
    "repair_expectation": "Correct the filename string to match the actual data file path in the project directory structure.",
    "validation_signal": "WSL Vitis/AIE compile failure or aiecompiler error indicating the specified data file cannot be found or opened.",
    "tags": ["filename", "missing_file", "path", "plio", "plio_ports"],
}


def _mutate_path(path_str: str) -> str:
    """Mutate a file path string to reference a nonexistent file."""
    # Strategy 1: If there's a directory separator, corrupt the directory
    if "/" in path_str:
        parts = path_str.rsplit("/", 1)
        dir_part = parts[0]
        file_part = parts[1]
        # Try corrupting the filename first (double a letter)
        if len(file_part) > 0:
            # Find the base name (before extension)
            dot_idx = file_part.rfind(".")
            if dot_idx > 0:
                base = file_part[:dot_idx]
                ext = file_part[dot_idx:]
                # Double the last character of the base name
                mutated_file = base + base[-1] + ext
                return dir_part + "/" + mutated_file
        # Fallback: corrupt directory name
        return dir_part + "x/" + file_part
    else:
        # No directory, just corrupt the filename
        dot_idx = path_str.rfind(".")
        if dot_idx > 0:
            base = path_str[:dot_idx]
            ext = path_str[dot_idx:]
            return base + base[-1] + ext
        return path_str + "_bad"


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find PLIO create calls with file path strings that can be mutated."""
    candidates: list[dict[str, object]] = []

    # Match input_plio::create or output_plio::create with a string argument containing .txt or .dat
    plio_pattern = re.compile(
        r'((?:input_plio|output_plio)\s*::\s*create\s*\(\s*"[^"]*"'
        r'(?:\s*,\s*"([^"]*\.(?:txt|dat))")'
        r')'
    )

    # Also match the simpler pattern where the file path is the first string arg
    # or patterns like: input_plio::create("name", "path/file.txt", ...)
    # More general: find any plio create call and extract file path strings
    general_pattern = re.compile(
        r'((?:input_plio|output_plio)\s*::\s*create\s*\([^)]*\))'
    )

    # Pattern to find quoted strings ending in .txt or .dat within a plio create call
    file_string_pattern = re.compile(r'"([^"]*\.(?:txt|dat))"')

    # Target files: graph headers (.h, .hpp) and graph sources (.cpp, .cc)
    target_extensions = (".h", ".hpp", ".cpp", ".cc", ".c")

    for file_path, content in project_files.items():
        # Check if this looks like a graph header or source file
        if not any(file_path.endswith(ext) for ext in target_extensions):
            continue

        # Check if file contains plio create calls
        if "plio" not in content.lower() and "PLIO" not in content:
            continue

        # Find all plio create calls
        for match in general_pattern.finditer(content):
            call_text = match.group(0)
            call_start = match.start()

            # Find file path strings within this call
            for file_match in file_string_pattern.finditer(call_text):
                file_path_str = file_match.group(1)
                # Calculate absolute positions in the file
                # The quoted string including quotes
                quoted_original = '"' + file_path_str + '"'
                abs_start = call_start + file_match.start()
                abs_end = call_start + file_match.end()

                mutated_path = _mutate_path(file_path_str)
                if mutated_path == file_path_str:
                    continue

                quoted_replacement = '"' + mutated_path + '"'

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "plio_filename_path_mismatch",
                    "category": "plio_ports",
                    "start": abs_start,
                    "end": abs_end,
                    "original": quoted_original,
                    "replacement": quoted_replacement,
                    "description": (
                        f"Changed PLIO data file path from '{file_path_str}' "
                        f"to '{mutated_path}' to reference a nonexistent file."
                    ),
                })

    return candidates


def apply_mutation(
    project_files: dict[str, str], candidate: dict[str, object]
) -> dict[str, str]:
    """Apply a mutation candidate to produce a new set of project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
