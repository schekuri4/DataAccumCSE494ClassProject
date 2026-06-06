BUG_FAMILY = {
    "family_id": "BF144",
    "bug_type": "cascade_acc48_vs_acc80_width_mismatch",
    "category": "cascade_streams",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "acc48", "acc80",
        "input_cascade<acc48>", "input_cascade<acc80>",
        "output_cascade<acc48>", "output_cascade<acc80>",
        "cacc48", "cacc80"
    ],
    "mutation_strategy": "Change the accumulator type in a cascade port from acc48 to acc80 (or vice versa) in the kernel signature without updating the corresponding graph port type or the connected kernel's complementary cascade port type, creating a width mismatch.",
    "repair_expectation": "Ensure consistent accumulator width (acc48 or acc80) across the kernel signature, graph port declaration, and the connected kernel's corresponding cascade port.",
    "validation_signal": "WSL Vitis/AIE compile failure with cascade accumulator width mismatch or template argument incompatibility from aiecompiler.",
    "tags": ["acc48", "acc80", "accumulator", "cascade", "cascade_streams", "width_mismatch"]
}

import re
import copy


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find cascade accumulator type occurrences that can be mutated to create width mismatches."""
    candidates = []

    # Patterns to match cascade port types with accumulator widths
    # Matches: input_cascade<acc48>, input_cascade<acc80>, output_cascade<acc48>, output_cascade<acc80>
    # Also matches cacc48, cacc80, and standalone acc48/acc80 in cascade contexts
    patterns = [
        # input_cascade<accXX> or output_cascade<accXX>
        (re.compile(r'(input_cascade|output_cascade)\s*<\s*(acc48|acc80|cacc48|cacc80)\s*>'), 'cascade_port'),
        # Standalone cacc48/cacc80 (complex accumulator types used in cascade)
        (re.compile(r'\b(cacc48|cacc80)\b'), 'cacc_type'),
        # acc48/acc80 within angle brackets (template arguments for cascade)
        (re.compile(r'<\s*(acc48|acc80)\s*>'), 'acc_template_arg'),
    ]

    # Target file extensions for kernel source and headers
    target_extensions = ('.h', '.hpp', '.cc', '.cpp', '.c', '.hxx', '.cxx')

    for file_path, content in project_files.items():
        if not any(file_path.endswith(ext) for ext in target_extensions):
            continue

        for pattern, pattern_type in patterns:
            for match in pattern.finditer(content):
                if pattern_type == 'cascade_port':
                    # Group 2 is the accumulator type
                    acc_type = match.group(2)
                    acc_start = match.start(2)
                    acc_end = match.end(2)
                elif pattern_type == 'cacc_type':
                    acc_type = match.group(1)
                    acc_start = match.start(1)
                    acc_end = match.end(1)
                elif pattern_type == 'acc_template_arg':
                    acc_type = match.group(1)
                    acc_start = match.start(1)
                    acc_end = match.end(1)
                else:
                    continue

                # Determine replacement: swap 48 <-> 80
                if acc_type in ('acc48', 'cacc48'):
                    replacement = acc_type.replace('48', '80')
                elif acc_type in ('acc80', 'cacc80'):
                    replacement = acc_type.replace('80', '48')
                else:
                    continue

                original = acc_type
                description = (
                    f"Changed cascade accumulator type from '{original}' to '{replacement}' "
                    f"in {file_path} at position {acc_start}, creating a width mismatch "
                    f"between connected cascade ports."
                )

                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": acc_start,
                    "end": acc_end,
                    "original": original,
                    "replacement": replacement,
                    "description": description,
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]

    new_project_files[file_path] = new_content
    return new_project_files
