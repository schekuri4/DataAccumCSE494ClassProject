import re
import copy

BUG_FAMILY = {
    "family_id": "BF002",
    "bug_type": "missing_adf_header_for_graph_definition",
    "category": "include_headers",
    "target_files": [
        "graph header",
        "graph source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include <adf.h>",
        "adf::graph",
        "adf::kernel",
        "adf::connect",
        "adf::PLIO",
        "adf::GMIO"
    ],
    "mutation_strategy": "Remove #include <adf.h> from the graph header or source file that defines a class inheriting from adf::graph, uses kernel::create, connect<>, PLIO, or GMIO declarations, causing all ADF API symbols to be undeclared.",
    "repair_expectation": "Restore #include <adf.h> at the top of the graph header/source file.",
    "validation_signal": "WSL Vitis/AIE compile failure with errors like 'adf has not been declared' or 'expected class-name before { token'.",
    "tags": [
        "adf",
        "gmio",
        "graph",
        "include_headers",
        "missing_include",
        "plio"
    ]
}

# Pattern to match #include <adf.h> with optional surrounding whitespace on the line
_INCLUDE_ADF_PATTERN = re.compile(r'^[ \t]*#\s*include\s*<\s*adf\.h\s*>[ \t]*\r?\n?', re.MULTILINE)

# Patterns that indicate the file uses ADF graph-related API
_ADF_USAGE_PATTERNS = [
    re.compile(r'adf\s*::\s*graph'),
    re.compile(r'adf\s*::\s*kernel'),
    re.compile(r'adf\s*::\s*connect'),
    re.compile(r'adf\s*::\s*PLIO'),
    re.compile(r'adf\s*::\s*GMIO'),
]


def _is_graph_file(file_path):
    """Check if file is likely a graph header or source file based on extension."""
    lower = file_path.lower()
    return lower.endswith(('.h', '.hpp', '.hxx', '.cpp', '.cc', '.cxx', '.c'))


def _uses_adf_api(content):
    """Check if file content uses any ADF API symbols."""
    for pattern in _ADF_USAGE_PATTERNS:
        if pattern.search(content):
            return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Find all #include <adf.h> occurrences in this file
        matches = list(_INCLUDE_ADF_PATTERN.finditer(content))
        if not matches:
            continue

        # Verify the file actually uses ADF API (graph definition context)
        if not _uses_adf_api(content):
            continue

        # Create a candidate for each #include <adf.h> found (typically one)
        for match in matches:
            original_text = match.group(0)
            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "missing_adf_header_for_graph_definition",
                "category": "include_headers",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": "",
                "description": (
                    f"Remove '#include <adf.h>' from '{file_path}' which defines/uses "
                    f"adf::graph or related ADF API symbols, causing compile failure due to "
                    f"undeclared ADF namespace symbols."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply the mutation by removing the #include <adf.h> line."""
    mutated_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    original_content = mutated_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    expected_original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the content at the expected location matches
    actual_text = original_content[start:end]
    if actual_text != expected_original:
        # Fallback: use regex to find and remove the first occurrence
        new_content = _INCLUDE_ADF_PATTERN.sub("", original_content, count=1)
    else:
        new_content = original_content[:start] + replacement + original_content[end:]

    mutated_files[file_path] = new_content
    return mutated_files
