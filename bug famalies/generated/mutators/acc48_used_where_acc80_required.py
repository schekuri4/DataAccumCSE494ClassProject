BUG_FAMILY = {
    "family_id": "BF231",
    "bug_type": "acc48_used_where_acc80_required",
    "category": "accumulator_types",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::accum<acc80",
        "aie::accum<acc48",
        "aie::mac",
        "aie::mul"
    ],
    "mutation_strategy": "Replace aie::accum<acc80, N> declarations with aie::accum<acc48, N> where the multiplication intrinsic or data types (e.g., int32*int32) require 80-bit accumulation precision, causing a type mismatch at the assignment or return statement.",
    "repair_expectation": "Change the accumulator type back to acc80 (or the appropriate wider type) to match the precision requirements of the multiply-accumulate operation.",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or type conversion error involving accumulator width mismatch.",
    "tags": [
        "acc48",
        "acc80",
        "accumulator_types",
        "multiply_accumulate",
        "precision_mismatch",
        "type_error"
    ]
}

import re
from copy import deepcopy


def _is_kernel_source(filepath):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    """Find all aie::accum<acc80, N> occurrences in kernel source files."""
    candidates = []
    # Pattern matches aie::accum<acc80, N> with optional whitespace variations
    pattern = re.compile(r'aie::accum\s*<\s*acc80\s*,\s*(\d+)\s*>')

    for filepath, content in project_files.items():
        if not _is_kernel_source(filepath):
            continue

        # Check if file has mac/mul operations suggesting accumulator usage
        has_mac_mul = bool(re.search(r'aie::(mac|mul)', content))

        for match in pattern.finditer(content):
            lanes = match.group(1)
            original = match.group(0)
            # Build the replacement: acc80 -> acc48
            replacement = re.sub(r'acc80', 'acc48', original)

            start = match.start()
            end = match.end()

            description = (
                f"Replace aie::accum<acc80, {lanes}> with aie::accum<acc48, {lanes}> "
                f"causing precision mismatch with multiply-accumulate operations"
            )
            if not has_mac_mul:
                description += " (no explicit aie::mac/mul found but acc80 suggests wide accumulation needed)"

            candidates.append({
                "file_path": filepath,
                "bug_type": "acc48_used_where_acc80_required",
                "category": "accumulator_types",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": description
            })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = project_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    actual_text = content[start:end]
    if actual_text == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[filepath] = new_content
    return new_project_files
