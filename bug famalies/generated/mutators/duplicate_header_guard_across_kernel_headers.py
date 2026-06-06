import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF006",
    "bug_type": "duplicate_header_guard_across_kernel_headers",
    "category": "include_headers",
    "target_files": [
        "kernel header",
        "shared utility header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#ifndef",
        "#define",
        "#pragma once",
        "_KERNEL_H_",
        "_KERNELS_H_"
    ],
    "mutation_strategy": "Set the same #ifndef/#define header guard macro name (e.g., __KERNEL_H__) in two different kernel header files, so that when both are included from the graph header, the second header's contents are silently skipped, causing undeclared kernel function prototypes used in kernel::create.",
    "repair_expectation": "Give each kernel header a unique header guard macro name matching its filename.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'was not declared in this scope' for kernel functions defined in the second header.",
    "tags": [
        "duplicate_guard",
        "header_guard",
        "include_headers",
        "kernel_create",
        "silent_skip"
    ]
}

# Regex to detect traditional header guards: #ifndef MACRO / #define MACRO
_GUARD_PATTERN = re.compile(
    r'^[ \t]*(#\s*ifndef\s+)(\w+)([ \t]*\n[ \t]*#\s*define\s+)(\w+)',
    re.MULTILINE
)

# Regex to detect #pragma once
_PRAGMA_ONCE_PATTERN = re.compile(
    r'^[ \t]*#\s*pragma\s+once[ \t]*$',
    re.MULTILINE
)


def _is_header_file(path: str) -> bool:
    """Check if a file looks like a C/C++ header."""
    return path.endswith('.h') or path.endswith('.hpp') or path.endswith('.hh')


def _is_kernel_header(path: str, content: str) -> bool:
    """Heuristic: a header that likely declares kernel functions for AIE."""
    # Check filename hints or content hints
    lower_path = path.lower()
    if 'kernel' in lower_path:
        return True
    # Check if it contains function declarations that look like kernel prototypes
    if re.search(r'\bvoid\b.*\b(input_window|output_window|input_stream|output_stream|adf::)', content):
        return True
    # Also consider shared utility headers
    if 'util' in lower_path or 'shared' in lower_path or 'common' in lower_path:
        return True
    return False


def _extract_guard_info(content: str) -> dict[str, Any] | None:
    """Extract header guard macro and positions from file content."""
    m = _GUARD_PATTERN.search(content)
    if m:
        ifndef_macro = m.group(2)
        define_macro = m.group(4)
        if ifndef_macro == define_macro:
            return {
                "type": "ifndef_define",
                "macro": ifndef_macro,
                "match": m,
                "ifndef_start": m.start(2),
                "ifndef_end": m.end(2),
                "define_start": m.start(4),
                "define_end": m.end(4),
            }
    # Check for pragma once
    pm = _PRAGMA_ONCE_PATTERN.search(content)
    if pm:
        return {
            "type": "pragma_once",
            "macro": None,
            "match": pm,
            "pragma_start": pm.start(),
            "pragma_end": pm.end(),
        }
    return None


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find pairs of kernel headers where we can make their guards identical."""
    candidates: list[dict[str, object]] = []

    # Collect all header files that look like kernel/utility headers with guards
    header_infos: list[tuple[str, str, dict]] = []
    for path, content in project_files.items():
        if not _is_header_file(path):
            continue
        if not _is_kernel_header(path, content):
            continue
        guard_info = _extract_guard_info(content)
        if guard_info is not None:
            header_infos.append((path, content, guard_info))

    # If we don't have kernel-specific headers, fall back to any headers with guards
    if len(header_infos) < 2:
        header_infos = []
        for path, content in project_files.items():
            if not _is_header_file(path):
                continue
            guard_info = _extract_guard_info(content)
            if guard_info is not None:
                header_infos.append((path, content, guard_info))

    if len(header_infos) < 2:
        return []

    # Sort for determinism
    header_infos.sort(key=lambda x: x[0])

    # For each pair, create a candidate that changes the second header's guard
    # to match the first header's guard
    for i in range(len(header_infos)):
        for j in range(i + 1, len(header_infos)):
            path_a, content_a, info_a = header_infos[i]
            path_b, content_b, info_b = header_infos[j]

            # We'll mutate the second file's guard to match the first file's guard
            if info_a["type"] == "ifndef_define" and info_b["type"] == "ifndef_define":
                # Both have traditional guards - change B's macro to match A's
                if info_a["macro"] == info_b["macro"]:
                    continue  # Already the same, skip

                target_macro = info_a["macro"]
                original_macro = info_b["macro"]

                # Build the replacement: replace all occurrences of the guard macro in file B
                # We need to replace in #ifndef and #define lines, and possibly #endif comment
                # For simplicity, describe the mutation on the ifndef line
                match_b = info_b["match"]
                original_text = match_b.group(0)
                replacement_text = original_text.replace(original_macro, target_macro)

                candidate = {
                    "file_path": path_b,
                    "bug_type": "duplicate_header_guard_across_kernel_headers",
                    "category": "include_headers",
                    "start": match_b.start(),
                    "end": match_b.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Changed header guard in '{path_b}' from '{original_macro}' "
                        f"to '{target_macro}' (same as '{path_a}'), causing the second "
                        f"included header's contents to be silently skipped."
                    ),
                    # Extra metadata for apply_mutation to handle full replacement
                    "_original_macro": original_macro,
                    "_target_macro": target_macro,
                }
                candidates.append(candidate)

            elif info_a["type"] == "pragma_once" and info_b["type"] == "pragma_once":
                # Both use pragma once - convert both to #ifndef with same macro
                # Mutate file B: replace #pragma once with a guard that duplicates
                # what we'll also put in file A... but that changes two files.
                # Instead: convert B's pragma once to a traditional guard matching
                # a common name, and also convert A's pragma once to the same guard.
                # Actually, for pragma once, the compiler handles it per-file, so
                # duplicating pragma once doesn't cause the bug. We need traditional guards.
                # Strategy: convert file B's #pragma once to a traditional guard,
                # using a macro name that matches file A's (after also converting A).
                # This requires mutating two files. Let's create a candidate that
                # converts both.
                common_macro = "__KERNEL_H__"
                # We'll describe this as a two-file mutation
                # But our interface only supports single file_path...
                # Let's create a candidate per file that converts pragma once to a
                # traditional guard with a common name. The pair together causes the bug.
                # For now, skip pragma_once pairs - focus on ifndef/define pairs
                continue

            elif info_a["type"] == "ifndef_define" and info_b["type"] == "pragma_once":
                # Convert B's pragma once to a traditional guard matching A's macro
                target_macro = info_a["macro"]
                pragma_match = info_b["match"]
                original_text = pragma_match.group(0)
                replacement_text = f"#ifndef {target_macro}\n#define {target_macro}"

                candidate = {
                    "file_path": path_b,
                    "bug_type": "duplicate_header_guard_across_kernel_headers",
                    "category": "include_headers",
                    "start": pragma_match.start(),
                    "end": pragma_match.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Replaced '#pragma once' in '{path_b}' with '#ifndef {target_macro}' / "
                        f"'#define {target_macro}' (same guard as '{path_a}'), causing the second "
                        f"included header's contents to be silently skipped."
                    ),
                    "_original_macro": None,
                    "_target_macro": target_macro,
                    "_pragma_to_guard": True,
                }
                candidates.append(candidate)

            elif info_a["type"] == "pragma_once" and info_b["type"] == "ifndef_define":
                # Convert A's pragma once to match B's guard - but we mutate A here
                target_macro = info_b["macro"]
                pragma_match = info_a["match"]
                original_text = pragma_match.group(0)
                replacement_text = f"#ifndef {target_macro}\n#define {target_macro}"

                candidate = {
                    "file_path": path_a,
                    "bug_type": "duplicate_header_guard_across_kernel_headers",
                    "category": "include_headers",
                    "start": pragma_match.start(),
                    "end": pragma_match.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Replaced '#pragma once' in '{path_a}' with '#ifndef {target_macro}' / "
                        f"'#define {target_macro}' (same guard as '{path_b}'), causing the second "
                        f"included header's contents to be silently skipped."
                    ),
                    "_original_macro": None,
                    "_target_macro": target_macro,
                    "_pragma_to_guard": True,
                }
                candidates.append(candidate)

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply the mutation to produce a new set of project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    original_macro = candidate.get("_original_macro")
    target_macro = candidate.get("_target_macro")
    is_pragma_to_guard = candidate.get("_pragma_to_guard", False)

    if is_pragma_to_guard:
        # Replace #pragma once with the traditional guard
        original = candidate["original"]
        replacement = candidate["replacement"]
        new_content = content[:candidate["start"]] + replacement + content[candidate["end"]:]

        # Also need to add #endif at the end of the file if converting from pragma once
        if not new_content.rstrip().endswith("#endif"):
            new_content = new_content.rstrip() + "\n\n#endif\n"

        new_files[file_path] = new_content
    else:
        # Replace the guard macro throughout the file
        # Replace all occurrences of the original macro with the target macro
        # but only the guard-related ones (ifndef, define, endif comment)
        if original_macro and target_macro:
            # Replace in the guard pattern area and any #endif /* MACRO */ comments
            new_content = content[:candidate["start"]] + candidate["replacement"] + content[candidate["end"]:]

            # Also replace any other occurrences of the guard macro in the file
            # (e.g., in #endif /* ORIGINAL_MACRO */ comments)
            # We do this after the main replacement to avoid offset issues
            # Since we already replaced the ifndef/define block, handle the rest
            remaining_after = new_content[candidate["start"] + len(candidate["replacement"]):]
            remaining_after = remaining_after.replace(original_macro, target_macro)
            new_content = new_content[:candidate["start"] + len(candidate["replacement"])] + remaining_after

            new_files[file_path] = new_content
        else:
            # Fallback: simple text replacement at the specified position
            new_content = content[:candidate["start"]] + candidate["replacement"] + content[candidate["end"]:]
            new_files[file_path] = new_content

    return new_files
