import re
import copy

BUG_FAMILY = {
    "family_id": "BF042",
    "bug_type": "kernel_source_extension_mismatch",
    "category": "kernel_source_paths",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::source(",
        ".cc\"",
        ".cpp\"",
        ".h\""
    ],
    "mutation_strategy": "Change the file extension in the adf::source() path string from the correct extension (e.g., '.cc') to a wrong one (e.g., '.cpp', '.c', or '.h'), where the actual file on disk uses the original extension.",
    "repair_expectation": "Change the extension back to match the actual kernel source file extension on disk.",
    "validation_signal": "WSL Vitis/AIE compile failure reporting that the specified source file does not exist or cannot be opened.",
    "tags": [
        "adf_source",
        "compile_error",
        "extension_mismatch",
        "file_not_found",
        "kernel_source_paths"
    ]
}

# Map from original extension to list of possible wrong extensions
_EXTENSION_ALTERNATIVES = {
    ".cc": [".cpp", ".c", ".h"],
    ".cpp": [".cc", ".c", ".h"],
    ".c": [".cc", ".cpp", ".h"],
    ".h": [".cc", ".cpp", ".c"],
}

# Pattern to match adf::source(...) calls with a string literal containing a file path
_ADF_SOURCE_PATTERN = re.compile(
    r'(adf::source\s*\(\s*[^)]*?\s*"([^"]*\.(cc|cpp|c|h))")'
)


def _is_graph_file(file_path):
    """Heuristic to identify graph header or graph source files."""
    lower = file_path.lower()
    # Common patterns for graph files in AIE projects
    if 'graph' in lower:
        return True
    # Also consider .h and .cpp files that might be graph definitions
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Search for all adf::source() calls with file path strings
        for match in _ADF_SOURCE_PATTERN.finditer(content):
            full_match = match.group(1)
            file_ref = match.group(2)
            ext = "." + match.group(3)

            # Get alternative extensions
            alternatives = _EXTENSION_ALTERNATIVES.get(ext, [])
            if not alternatives:
                continue

            # Use the first alternative as the replacement
            new_ext = alternatives[0]

            # Find the exact position of the extension within the quoted string
            # We want to replace just the extension in the path
            # Find the quoted path string within the match
            quote_pattern = re.compile(re.escape('"' + file_ref + '"'))
            path_match = quote_pattern.search(content, match.start())
            if not path_match:
                continue

            original_str = '"' + file_ref + '"'
            new_file_ref = file_ref[:-len(ext)] + new_ext
            replacement_str = '"' + new_file_ref + '"'

            start_pos = path_match.start()
            end_pos = path_match.end()

            candidate = {
                "file_path": file_path,
                "bug_type": "kernel_source_extension_mismatch",
                "category": "kernel_source_paths",
                "start": start_pos,
                "end": end_pos,
                "original": original_str,
                "replacement": replacement_str,
                "description": (
                    f"Changed kernel source extension in adf::source() from "
                    f"'{ext}' to '{new_ext}' in path '{file_ref}', "
                    f"causing a file-not-found error at compile time."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
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
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
