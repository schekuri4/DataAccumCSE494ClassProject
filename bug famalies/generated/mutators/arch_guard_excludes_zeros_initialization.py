import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF015",
    "bug_type": "arch_guard_excludes_zeros_initialization",
    "category": "header_guards_and_preprocessor",
    "target_files": [
        "kernel source",
        "shared utility header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::zeros",
        "aie::broadcast",
        "#if __AIE_ARCH__ ==",
        "aie::accum"
    ],
    "mutation_strategy": "Place aie::zeros<>() accumulator initialization inside an #if __AIE_ARCH__ == 2 block when the target is AIE1 (arch version 1), causing the initialization to be skipped and the accumulator variable to be uninitialized or undeclared, or replace with an invalid aie::broadcast call that doesn't compile.",
    "repair_expectation": "Change the architecture guard to match the actual target architecture (e.g., __AIE_ARCH__ == 1) or remove the guard so aie::zeros is always available.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared variable or no matching function for aie::zeros/broadcast.",
    "tags": [
        "accumulator",
        "aie_arch",
        "aie_zeros",
        "arch_guard",
        "broadcast",
        "header_guards_and_preprocessor"
    ]
}


def _is_target_file(file_path: str) -> bool:
    """Check if file is a kernel source or shared utility header."""
    extensions = ('.cc', '.cpp', '.c', '.h', '.hpp', '.hh')
    return file_path.endswith(extensions)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match lines containing aie::zeros initialization statements
    # e.g., "aie::accum<accfloat, 8> acc = aie::zeros<accfloat, 8>();"
    # or "auto acc = aie::zeros<int32, 16>();"
    # or just "aie::zeros<...>(...)"
    zeros_pattern = re.compile(
        r'^([ \t]*)(.*aie::zeros\s*<[^>]*>\s*\([^)]*\)\s*;.*?)$',
        re.MULTILINE
    )

    # Also match standalone assignment with aie::zeros
    assign_zeros_pattern = re.compile(
        r'^([ \t]*)(.+?=\s*aie::zeros\s*<[^>]*>\s*\([^)]*\)\s*;)(.*)$',
        re.MULTILINE
    )

    # Pattern for aie::broadcast used for initialization
    broadcast_pattern = re.compile(
        r'^([ \t]*)(.+?=\s*aie::broadcast\s*<[^>]*>\s*\([^)]*\)\s*;)(.*)$',
        re.MULTILINE
    )

    for file_path, content in project_files.items():
        if not _is_target_file(file_path):
            continue

        # Check if file contains any of our match targets
        has_zeros = 'aie::zeros' in content
        has_broadcast = 'aie::broadcast' in content

        if not (has_zeros or has_broadcast):
            continue

        # Find aie::zeros statements to wrap in wrong arch guard
        for match in assign_zeros_pattern.finditer(content):
            indent = match.group(1)
            statement = match.group(2)
            trailing = match.group(3)
            full_match = match.group(0)
            start = match.start()
            end = match.end()

            # Skip if already inside an arch guard (simple heuristic: check preceding lines)
            preceding = content[max(0, start - 200):start]
            if '#if __AIE_ARCH__' in preceding and '#endif' not in preceding:
                continue

            # Create mutation: wrap in wrong architecture guard
            replacement = (
                f"{indent}#if __AIE_ARCH__ == 2\n"
                f"{indent}{statement}{trailing}\n"
                f"{indent}#endif"
            )

            candidates.append({
                "file_path": file_path,
                "bug_type": "arch_guard_excludes_zeros_initialization",
                "category": "header_guards_and_preprocessor",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": (
                    f"Wrapped aie::zeros initialization in '#if __AIE_ARCH__ == 2' guard, "
                    f"causing it to be excluded on AIE1 targets where __AIE_ARCH__ == 1. "
                    f"File: {file_path}"
                )
            })

        # Find aie::broadcast statements similarly
        for match in broadcast_pattern.finditer(content):
            indent = match.group(1)
            statement = match.group(2)
            trailing = match.group(3)
            full_match = match.group(0)
            start = match.start()
            end = match.end()

            preceding = content[max(0, start - 200):start]
            if '#if __AIE_ARCH__' in preceding and '#endif' not in preceding:
                continue

            replacement = (
                f"{indent}#if __AIE_ARCH__ == 2\n"
                f"{indent}{statement}{trailing}\n"
                f"{indent}#endif"
            )

            candidates.append({
                "file_path": file_path,
                "bug_type": "arch_guard_excludes_zeros_initialization",
                "category": "header_guards_and_preprocessor",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": (
                    f"Wrapped aie::broadcast initialization in '#if __AIE_ARCH__ == 2' guard, "
                    f"causing it to be excluded on AIE1 targets. File: {file_path}"
                )
            })

        # Also handle cases where aie::zeros appears in a more general statement
        # (not caught by assign pattern) using the simpler zeros_pattern
        if not candidates or not any(c["file_path"] == file_path for c in candidates):
            for match in zeros_pattern.finditer(content):
                indent = match.group(1)
                statement = match.group(2)
                full_match = match.group(0)
                start = match.start()
                end = match.end()

                preceding = content[max(0, start - 200):start]
                if '#if __AIE_ARCH__' in preceding and '#endif' not in preceding:
                    continue

                replacement = (
                    f"{indent}#if __AIE_ARCH__ == 2\n"
                    f"{indent}{statement}\n"
                    f"{indent}#endif"
                )

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "arch_guard_excludes_zeros_initialization",
                    "category": "header_guards_and_preprocessor",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": (
                        f"Wrapped aie::zeros call in '#if __AIE_ARCH__ == 2' guard, "
                        f"causing it to be excluded on AIE1 targets. File: {file_path}"
                    )
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    original_content = new_project_files[file_path]

    original_text = candidate["original"]
    replacement_text = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if original_content[start:end] == original_text:
        new_content = original_content[:start] + replacement_text + original_content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = original_content.replace(original_text, replacement_text, 1)

    new_project_files[file_path] = new_content
    return new_project_files
