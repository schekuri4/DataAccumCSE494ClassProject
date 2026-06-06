import re
import copy

BUG_FAMILY = {
    "family_id": "BF005",
    "bug_type": "missing_aie_api_aie_adf_bridge_header",
    "category": "include_headers",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include <aie_api/utils.hpp>",
        "readincr_v",
        "writeincr_v",
        "#include <aie_api/aie_adf.hpp>"
    ],
    "mutation_strategy": "Remove #include <aie_api/aie_adf.hpp> from a kernel source that uses readincr_v<N>() or writeincr_v<N>() stream access functions, which require the AIE-ADF bridge header for proper type resolution between ADF stream types and aie::vector types.",
    "repair_expectation": "Add #include <aie_api/aie_adf.hpp> to the kernel source file.",
    "validation_signal": "WSL Vitis/AIE compile failure with errors about readincr_v or writeincr_v not being declared or template argument deduction failure.",
    "tags": [
        "aie_adf",
        "include_headers",
        "missing_include",
        "readincr_v",
        "stream",
        "writeincr_v"
    ]
}

# Pattern to match the #include <aie_api/aie_adf.hpp> line (with optional surrounding whitespace)
_INCLUDE_PATTERN = re.compile(r'^[ \t]*#\s*include\s*<aie_api/aie_adf\.hpp>\s*\n?', re.MULTILINE)

# Patterns to detect usage of readincr_v or writeincr_v
_USAGE_PATTERN = re.compile(r'\b(readincr_v|writeincr_v)\b')


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic: kernel sources are .cpp, .cc, or .h files typically in kernel/src directories."""
    lower = file_path.lower()
    return lower.endswith(('.cpp', '.cc', '.h', '.hpp'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file contains the aie_adf.hpp include
        include_match = _INCLUDE_PATTERN.search(content)
        if not include_match:
            continue

        # Check if file uses readincr_v or writeincr_v
        if not _USAGE_PATTERN.search(content):
            continue

        # Found a valid mutation site
        original = include_match.group(0)
        start = include_match.start()
        end = include_match.end()

        candidates.append({
            "file_path": file_path,
            "bug_type": BUG_FAMILY["bug_type"],
            "category": BUG_FAMILY["category"],
            "start": start,
            "end": end,
            "original": original,
            "replacement": "",
            "description": (
                f"Remove '#include <aie_api/aie_adf.hpp>' from {file_path} which uses "
                f"readincr_v/writeincr_v stream access functions, causing compile failure "
                f"due to missing type bridge declarations."
            )
        })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_project_files = dict(project_files)

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use regex to find and remove the first occurrence
        new_content = _INCLUDE_PATTERN.sub("", content, count=1)

    new_project_files[file_path] = new_content
    return new_project_files
