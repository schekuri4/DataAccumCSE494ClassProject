import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF088",
    "bug_type": "gmio_filename_path_mismatch_in_sim",
    "category": "gmio_ports",
    "target_files": ["graph source", "graph header"],
    "artifact_handling": "reference_missing_file",
    "match_targets": [
        "gm.gm2aie_nb",
        "gm.aie2gm_nb",
        "GMIO::gm2aie",
        "GMIO::aie2gm",
        "gmio_in",
        "gmio_out"
    ],
    "mutation_strategy": (
        "In the graph simulation testbench or GMIO data transfer calls, reference a data file path "
        "(e.g., 'data/input_gmio.txt') that does not exist or has a typo in the filename, causing "
        "a compile-time or early link-time failure when the path is validated or when the file is "
        "opened in a constexpr/static context."
    ),
    "repair_expectation": "Correct the file path string to match the actual data file location in the project.",
    "validation_signal": "WSL Vitis/AIE compile failure or aiesimulator pre-check failure due to missing referenced file.",
    "tags": [
        "compile_error",
        "data_file",
        "filename",
        "gmio",
        "gmio_ports",
        "path_mismatch"
    ]
}


def _is_target_file(filepath: str) -> bool:
    """Check if file is likely a graph source or graph header."""
    lower = filepath.lower()
    # Graph sources and headers typically have these extensions and may contain 'graph'
    if lower.endswith(('.cpp', '.cc', '.cxx', '.c', '.h', '.hpp', '.hxx')):
        return True
    return False


def _introduce_typo_in_path(path_str: str) -> str:
    """Introduce a typo in the filename portion of a path string to make it reference a missing file."""
    # Split on last '/' to get directory and filename
    if '/' in path_str:
        parts = path_str.rsplit('/', 1)
        directory = parts[0]
        filename = parts[1]
    else:
        directory = ""
        filename = path_str

    # Introduce typo in filename: insert 'x' before the extension or duplicate a char
    if '.' in filename:
        name_part, ext = filename.rsplit('.', 1)
        if len(name_part) > 0:
            # Insert '_typo' before extension
            mutated_filename = name_part + "_typo." + ext
        else:
            mutated_filename = "typo." + ext
    else:
        mutated_filename = filename + "_typo"

    if directory:
        return directory + "/" + mutated_filename
    return mutated_filename


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to find GMIO-related function calls that contain file path string literals
    # Matches patterns like: gm2aie_nb("data/input.txt", ...) or GMIO::gm2aie("path", ...)
    # Also matches generic file path strings near gmio keywords
    gmio_keywords = [
        r'gm2aie_nb', r'aie2gm_nb', r'gm2aie', r'aie2gm',
        r'gmio_in', r'gmio_out'
    ]

    # Pattern 1: GMIO call with a string literal that looks like a file path
    # e.g., something.gm2aie_nb("data/input.txt", size)
    call_pattern = re.compile(
        r'(' + '|'.join(gmio_keywords) + r')\s*\(\s*"([^"]*[/\\][^"]*\.[a-zA-Z]+)"',
        re.IGNORECASE
    )

    # Pattern 2: String literal assigned or used near GMIO context that looks like a file path
    # e.g., "data/input_gmio.txt" appearing in lines with gmio references
    path_in_string_pattern = re.compile(
        r'"([^"]*[/\\][^"]*\.[a-zA-Z]+)"'
    )

    for filepath, content in project_files.items():
        if not _is_target_file(filepath):
            continue

        # Check if file contains any GMIO-related keywords
        has_gmio_context = any(
            kw in content for kw in [
                'gm2aie_nb', 'aie2gm_nb', 'gm2aie', 'aie2gm',
                'gmio_in', 'gmio_out', 'GMIO', 'gmio'
            ]
        )
        if not has_gmio_context:
            continue

        # Strategy 1: Find direct GMIO calls with file path arguments
        for match in call_pattern.finditer(content):
            full_match_start = match.start()
            full_match_end = match.end()
            path_str = match.group(2)

            # Get the exact position of the path string (inside quotes)
            # Find the quoted path within the match
            quote_start = content.index('"' + path_str + '"', match.start())
            string_start = quote_start  # includes opening quote
            string_end = quote_start + len(path_str) + 2  # includes closing quote

            original = '"' + path_str + '"'
            mutated_path = _introduce_typo_in_path(path_str)
            replacement = '"' + mutated_path + '"'

            if original == replacement:
                continue

            candidates.append({
                "file_path": filepath,
                "bug_type": "gmio_filename_path_mismatch_in_sim",
                "category": "gmio_ports",
                "start": string_start,
                "end": string_end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Introduced typo in GMIO data file path: changed '{path_str}' to "
                    f"'{mutated_path}' causing a missing file reference."
                )
            })

        # Strategy 2: Find file path strings on lines containing GMIO keywords
        lines = content.split('\n')
        offset = 0
        for line in lines:
            line_has_gmio = any(
                kw in line for kw in [
                    'gm2aie_nb', 'aie2gm_nb', 'gm2aie', 'aie2gm',
                    'gmio_in', 'gmio_out', 'GMIO', 'gmio'
                ]
            )
            if line_has_gmio:
                for match in path_in_string_pattern.finditer(line):
                    path_str = match.group(1)
                    abs_start = offset + match.start()
                    abs_end = offset + match.end()

                    original = '"' + path_str + '"'
                    mutated_path = _introduce_typo_in_path(path_str)
                    replacement = '"' + mutated_path + '"'

                    if original == replacement:
                        continue

                    # Avoid duplicates from strategy 1
                    is_dup = any(
                        c["file_path"] == filepath and c["start"] == abs_start and c["end"] == abs_end
                        for c in candidates
                    )
                    if is_dup:
                        offset += len(line) + 1
                        continue

                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "gmio_filename_path_mismatch_in_sim",
                        "category": "gmio_ports",
                        "start": abs_start,
                        "end": abs_end,
                        "original": original,
                        "replacement": replacement,
                        "description": (
                            f"Introduced typo in GMIO data file path: changed '{path_str}' to "
                            f"'{mutated_path}' causing a missing file reference."
                        )
                    })
            offset += len(line) + 1

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual = content[start:end]
    if actual == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
