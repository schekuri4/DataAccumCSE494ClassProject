import re
import copy

BUG_FAMILY = {
    "family_id": "BF274",
    "bug_type": "accumulator_complex_scalar_mismatch",
    "category": "complex_datatypes",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::accumulator",
        "aie::accum",
        "acc48",
        "acc80",
        "cacc48",
        "cacc80",
        "aie::zeros"
    ],
    "mutation_strategy": "Replace a complex accumulator type (e.g., aie::accum<cacc48,8>) with a real accumulator type (e.g., aie::accum<acc48,8>) while the kernel continues to perform complex multiply-accumulate operations that produce complex results, or vice versa. Alternatively, initialize a cacc48 accumulator with aie::zeros<acc48,N>() instead of aie::zeros<cacc48,N>().",
    "repair_expectation": "Use the correct complex accumulator type (cacc48 or cacc80) that matches the complex arithmetic being performed, and ensure aie::zeros uses the matching accumulator tag.",
    "validation_signal": "WSL Vitis/AIE compile failure with template type mismatch or no viable conversion between real and complex accumulator types.",
    "tags": [
        "acc48",
        "acc80",
        "accumulator",
        "aie_zeros",
        "cacc48",
        "cacc80",
        "complex_datatypes",
        "complex_vs_real"
    ]
}

# Mapping for toggling between complex and real accumulator types
_TOGGLE_MAP = {
    "cacc48": "acc48",
    "cacc80": "acc80",
    "acc48": "cacc48",
    "acc80": "cacc80",
}


def _is_kernel_source(filepath):
    """Heuristic: kernel source files are .cc, .cpp, .h, .hpp files."""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp', '.hxx'))


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern 1: aie::accum<(c)acc(48|80), N>
    accum_pattern = re.compile(
        r'(aie::accum\s*<\s*)(cacc48|cacc80|acc48|acc80)(\s*,\s*\d+\s*>)'
    )

    # Pattern 2: aie::zeros<(c)acc(48|80), N>()
    zeros_pattern = re.compile(
        r'(aie::zeros\s*<\s*)(cacc48|cacc80|acc48|acc80)(\s*,\s*\d+\s*>\s*\(\s*\))'
    )

    # Pattern 3: standalone type declarations like "cacc48" or "acc48" used as type
    # e.g., aie::accumulator<cacc48, 8> or variable type annotations
    accumulator_pattern = re.compile(
        r'(aie::accumulator\s*<\s*)(cacc48|cacc80|acc48|acc80)(\s*,\s*\d+\s*>)'
    )

    for filepath, content in project_files.items():
        if not _is_kernel_source(filepath):
            continue

        # Search for aie::accum<...> patterns
        for m in accum_pattern.finditer(content):
            acc_type = m.group(2)
            replacement_type = _TOGGLE_MAP[acc_type]
            original = m.group(0)
            replacement = m.group(1) + replacement_type + m.group(3)
            candidates.append({
                "file_path": filepath,
                "bug_type": "accumulator_complex_scalar_mismatch",
                "category": "complex_datatypes",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Replace {acc_type} with {replacement_type} in aie::accum declaration, causing complex/real mismatch"
            })

        # Search for aie::zeros<...> patterns
        for m in zeros_pattern.finditer(content):
            acc_type = m.group(2)
            replacement_type = _TOGGLE_MAP[acc_type]
            original = m.group(0)
            replacement = m.group(1) + replacement_type + m.group(3)
            candidates.append({
                "file_path": filepath,
                "bug_type": "accumulator_complex_scalar_mismatch",
                "category": "complex_datatypes",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Replace {acc_type} with {replacement_type} in aie::zeros initialization, causing complex/real mismatch"
            })

        # Search for aie::accumulator<...> patterns
        for m in accumulator_pattern.finditer(content):
            acc_type = m.group(2)
            replacement_type = _TOGGLE_MAP[acc_type]
            original = m.group(0)
            replacement = m.group(1) + replacement_type + m.group(3)
            candidates.append({
                "file_path": filepath,
                "bug_type": "accumulator_complex_scalar_mismatch",
                "category": "complex_datatypes",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Replace {acc_type} with {replacement_type} in aie::accumulator declaration, causing complex/real mismatch"
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[filepath] = new_content
    return new_files
