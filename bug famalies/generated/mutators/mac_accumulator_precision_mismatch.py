import re
import copy

BUG_FAMILY = {
    "family_id": "BF252",
    "bug_type": "mac_accumulator_precision_mismatch",
    "category": "arithmetic_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::mac",
        "aie::accum<acc48",
        "aie::accum<acc80",
        "aie::accumulator"
    ],
    "mutation_strategy": "Pass an acc48 accumulator to aie::mac where the operand types require acc80 precision (e.g., int32 x int32 multiplication result needs acc80), or vice versa pass acc80 where only acc48 is valid for int16 x int16.",
    "repair_expectation": "Change the accumulator template parameter from acc48 to acc80 (or vice versa) to match the required precision for the given operand types.",
    "validation_signal": "WSL Vitis/AIE aiecompiler emits a compile-time error about incompatible accumulator precision or no matching function for aie::mac.",
    "tags": [
        "acc48",
        "acc80",
        "accumulator",
        "arithmetic_intrinsics",
        "mac",
        "precision_mismatch"
    ]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cc, .cpp, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match aie::accum<acc48, N> or aie::accum<acc80, N>
    accum_pattern = re.compile(
        r'(aie::accum\s*<\s*)(acc48|acc80)(\s*,\s*\d+\s*>)'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        for match in accum_pattern.finditer(content):
            original_text = match.group(0)
            prefix = match.group(1)
            acc_type = match.group(2)
            suffix = match.group(3)

            # Swap acc48 <-> acc80
            if acc_type == "acc48":
                new_acc_type = "acc80"
                description = (
                    "Changed acc48 to acc80 accumulator precision, creating a mismatch "
                    "if operand types (e.g., int16 x int16) only require acc48."
                )
            else:
                new_acc_type = "acc48"
                description = (
                    "Changed acc80 to acc48 accumulator precision, creating a mismatch "
                    "if operand types (e.g., int32 x int32) require acc80."
                )

            replacement_text = prefix + new_acc_type + suffix

            candidates.append({
                "file_path": file_path,
                "bug_type": "mac_accumulator_precision_mismatch",
                "category": "arithmetic_intrinsics",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": description
            })

    # Also look for aie::mac calls that use typed accumulators inline
    # Pattern: aie::mac(accum_var, ...) where accum_var is declared with acc48/acc80
    # We already cover the declaration sites above, but let's also check for
    # accumulator type aliases or direct template usage in mac calls
    mac_accum_pattern = re.compile(
        r'(aie::mac\s*<\s*)(acc48|acc80)(\s*[,>])'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        for match in mac_accum_pattern.finditer(content):
            original_text = match.group(0)
            prefix = match.group(1)
            acc_type = match.group(2)
            suffix = match.group(3)

            if acc_type == "acc48":
                new_acc_type = "acc80"
                description = (
                    "Changed acc48 to acc80 in aie::mac template parameter, "
                    "creating accumulator precision mismatch."
                )
            else:
                new_acc_type = "acc48"
                description = (
                    "Changed acc80 to acc48 in aie::mac template parameter, "
                    "creating accumulator precision mismatch."
                )

            replacement_text = prefix + new_acc_type + suffix

            # Avoid duplicates if same location
            already_exists = any(
                c["file_path"] == file_path and c["start"] == match.start()
                for c in candidates
            )
            if not already_exists:
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "mac_accumulator_precision_mismatch",
                    "category": "arithmetic_intrinsics",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": description
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
