import re
import copy

BUG_FAMILY = {
    "family_id": "BF110",
    "bug_type": "missing_stream_header_include_for_scalar_api",
    "category": "stream_scalar_interfaces",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include <adf.h>",
        '#include "adf.h"',
        "aie_api/aie.hpp",
        "readincr(",
        "writeincr(",
        "input_stream",
        "output_stream"
    ],
    "mutation_strategy": "Remove or comment out the #include <adf.h> or relevant AIE API header that provides the input_stream/output_stream types and readincr/writeincr declarations, causing undeclared identifier errors for all stream scalar APIs.",
    "repair_expectation": "Restore the correct #include <adf.h> or appropriate AIE header that declares the stream types and scalar stream APIs.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared identifier errors for input_stream, output_stream, readincr, or writeincr.",
    "tags": ["adf", "header", "include", "stream_api", "stream_scalar_interfaces", "undeclared"]
}


def _file_uses_stream_scalar_api(content):
    """Check if file uses stream scalar API functions/types."""
    patterns = [
        r'\breadincr\s*\(',
        r'\bwriteincr\s*\(',
        r'\binput_stream\b',
        r'\boutput_stream\b'
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True
    return False


def _is_kernel_file(filepath):
    """Heuristic: kernel source or header files (C/C++ with typical extensions)."""
    lower = filepath.lower()
    extensions = ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh')
    return any(lower.endswith(ext) for ext in extensions)


def find_mutation_candidates(project_files):
    candidates = []

    # Patterns for include lines that provide stream scalar API
    include_patterns = [
        # #include <adf.h>
        re.compile(r'^[ \t]*#\s*include\s*<\s*adf\.h\s*>.*$', re.MULTILINE),
        # #include "adf.h"
        re.compile(r'^[ \t]*#\s*include\s*"\s*adf\.h\s*".*$', re.MULTILINE),
        # #include <aie_api/aie.hpp> or similar
        re.compile(r'^[ \t]*#\s*include\s*[<"]\s*aie_api/aie\.hpp\s*[>"].*$', re.MULTILINE),
        # #include <adf/stream/types.h> or any adf/stream header
        re.compile(r'^[ \t]*#\s*include\s*[<"]\s*adf/stream[^>"]*[>"].*$', re.MULTILINE),
    ]

    for filepath, content in project_files.items():
        if not _is_kernel_file(filepath):
            continue

        # File must use stream scalar APIs to be a valid mutation target
        if not _file_uses_stream_scalar_api(content):
            continue

        for pattern in include_patterns:
            for match in pattern.finditer(content):
                original_line = match.group(0)
                start = match.start()
                end = match.end()

                # Replacement: comment out the include
                replacement = "// [MUTATED] " + original_line.lstrip()

                candidate = {
                    "file_path": filepath,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": start,
                    "end": end,
                    "original": original_line,
                    "replacement": replacement,
                    "description": (
                        f"Comment out '{original_line.strip()}' in '{filepath}' to cause "
                        f"undeclared identifier errors for stream scalar APIs "
                        f"(input_stream, output_stream, readincr, writeincr)."
                    )
                }
                candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)

    filepath = candidate["file_path"]
    content = new_project_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_project_files[filepath] = new_content
    return new_project_files
