import re
import copy

BUG_FAMILY = {
    "family_id": "BF001",
    "bug_type": "missing_aie_api_header_for_accumulator",
    "category": "include_headers",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include <aie_api/aie.hpp>",
        "aie::accum<acc48",
        "aie::accum<acc80",
        "aie::zeros"
    ],
    "mutation_strategy": "Remove or comment out the #include <aie_api/aie.hpp> line from a kernel source that uses aie::accum<acc48,...>, aie::accum<acc80,...>, or aie::zeros<>(), causing undeclared identifier errors for accumulator types and initialization functions.",
    "repair_expectation": "Re-add #include <aie_api/aie.hpp> before any usage of aie:: namespace types or functions.",
    "validation_signal": "WSL Vitis/AIE compile failure with errors such as 'no member named accum in namespace aie' or 'use of undeclared identifier zeros'.",
    "tags": ["acc48", "acc80", "accumulator", "aie_api", "include_headers", "missing_include"]
}

# Pattern to match the include line (with possible whitespace variations)
_INCLUDE_PATTERN = re.compile(r'^[ \t]*#\s*include\s*<aie_api/aie\.hpp>\s*$', re.MULTILINE)

# Patterns that indicate accumulator/zeros usage requiring the header
_USAGE_PATTERNS = [
    re.compile(r'aie::accum\s*<\s*acc48'),
    re.compile(r'aie::accum\s*<\s*acc80'),
    re.compile(r'aie::zeros'),
]


def _is_kernel_file(file_path: str) -> bool:
    """Heuristic: kernel sources/headers are .cpp, .cc, .h, .hpp files,
    typically in paths containing 'kernel' or 'aie', or simply C++ files."""
    lower = file_path.lower()
    # Check extension
    if not any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh')):
        return False
    return True


def _has_accumulator_usage(content: str) -> bool:
    """Check if file uses aie::accum<acc48,...>, aie::accum<acc80,...>, or aie::zeros."""
    for pattern in _USAGE_PATTERNS:
        if pattern.search(content):
            return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue

        # Check if file has the include
        include_match = _INCLUDE_PATTERN.search(content)
        if not include_match:
            continue

        # Check if file uses accumulator types or zeros
        if not _has_accumulator_usage(content):
            continue

        # Found a valid mutation site
        start = include_match.start()
        end = include_match.end()
        original = include_match.group(0)

        # Determine which usages are present for description
        usages = []
        if re.search(r'aie::accum\s*<\s*acc48', content):
            usages.append("aie::accum<acc48>")
        if re.search(r'aie::accum\s*<\s*acc80', content):
            usages.append("aie::accum<acc80>")
        if re.search(r'aie::zeros', content):
            usages.append("aie::zeros")

        description = (
            f"Remove '#include <aie_api/aie.hpp>' from {file_path} which uses "
            f"{', '.join(usages)}, causing undeclared identifier errors."
        )

        candidates.append({
            "file_path": file_path,
            "bug_type": BUG_FAMILY["bug_type"],
            "category": BUG_FAMILY["category"],
            "start": start,
            "end": end,
            "original": original,
            "replacement": "",  # Remove the line entirely
            "description": description,
        })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        # Remove the line; also remove trailing newline if present
        if end < len(content) and content[end] == '\n':
            end += 1
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: use string replacement for first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_project_files[file_path] = new_content
    return new_project_files
