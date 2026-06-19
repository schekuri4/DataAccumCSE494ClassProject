import re
import copy

BUG_FAMILY = {
    "family_id": "BF009",
    "bug_type": "wrong_shuffle_intrinsic_header_version",
    "category": "include_headers",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "shuffle_up",
        "shuffle_down",
        "#include <aie_api/aie.hpp>",
        "aie::shuffle_up",
        "aie::shuffle_down"
    ],
    "mutation_strategy": "Replace #include <aie_api/aie.hpp> with the older low-level #include <aie_intrin.h> or omit it entirely in a kernel that uses aie::shuffle_up or aie::shuffle_down vector operations, causing the high-level API shuffle functions to be undeclared while potentially exposing incompatible low-level intrinsic signatures.",
    "repair_expectation": "Include #include <aie_api/aie.hpp> which provides the aie::shuffle_up and aie::shuffle_down template functions.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'no member named shuffle_up in namespace aie' or similar undeclared identifier error.",
    "tags": ["aie_api", "include_headers", "intrinsics", "shuffle", "wrong_header"]
}

# Pattern to match the include directive for aie_api/aie.hpp
_INCLUDE_PATTERN = re.compile(
    r'^\s*#\s*include\s*(?:<\s*aie_api/aie\.hpp\s*>|"aie_api/aie\.hpp")',
    re.MULTILINE
)

# Patterns to detect shuffle usage
_SHUFFLE_PATTERNS = [
    re.compile(r'(?:::)?aie::shuffle_up'),
    re.compile(r'(?:::)?aie::shuffle_down'),
    re.compile(r'\bshuffle_up\b'),
    re.compile(r'\bshuffle_down\b'),
]

# Typical kernel file extensions
_KERNEL_EXTENSIONS = ('.cpp', '.cc', '.cxx', '.c', '.h', '.hpp', '.hxx')


def _file_is_kernel_source_or_header(path):
    """Heuristic: file has a C/C++ extension typical of AIE kernels."""
    return any(path.endswith(ext) for ext in _KERNEL_EXTENSIONS)


def _file_uses_shuffle(content):
    """Check if file content uses shuffle_up or shuffle_down."""
    for pat in _SHUFFLE_PATTERNS:
        if pat.search(content):
            return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _file_is_kernel_source_or_header(file_path):
            continue

        # File must use shuffle operations
        if not _file_uses_shuffle(content):
            continue

        # Find all occurrences of #include <aie_api/aie.hpp>
        for match in _INCLUDE_PATTERN.finditer(content):
            original = match.group(0)
            start = match.start()
            end = match.end()

            # Replacement option 1: replace with old low-level intrinsic header
            candidates.append({
                "file_path": file_path,
                "bug_type": "wrong_shuffle_intrinsic_header_version",
                "category": "include_headers",
                "start": start,
                "end": end,
                "original": original,
                "replacement": "#include <aie_intrin.h>",
                "description": (
                    f"Replace '{original.strip()}' with '#include <aie_intrin.h>' in "
                    f"'{file_path}'. The old low-level header does not provide "
                    f"aie::shuffle_up / aie::shuffle_down, causing compile failure."
                )
            })

            # Replacement option 2: omit the include entirely
            # Determine if there's a trailing newline to remove cleanly
            trailing = ""
            if end < len(content) and content[end] == '\n':
                trailing = "\n"

            candidates.append({
                "file_path": file_path,
                "bug_type": "wrong_shuffle_intrinsic_header_version",
                "category": "include_headers",
                "start": start,
                "end": end + len(trailing),
                "original": original + trailing,
                "replacement": "",
                "description": (
                    f"Remove '{original.strip()}' entirely from '{file_path}'. "
                    f"Without this header, aie::shuffle_up / aie::shuffle_down are undeclared."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected location
    if content[start:end] != original:
        # Fallback: try to find and replace the first occurrence
        idx = content.find(original)
        if idx == -1:
            # Cannot apply mutation; return unmodified
            return new_project_files
        start = idx
        end = idx + len(original)

    new_content = content[:start] + replacement + content[end:]
    new_project_files[file_path] = new_content

    return new_project_files
