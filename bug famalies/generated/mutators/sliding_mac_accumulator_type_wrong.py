BUG_FAMILY = {
    "family_id": "BF264",
    "bug_type": "sliding_mac_accumulator_type_wrong",
    "category": "sliding_mul_and_mac",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::sliding_mac_ops",
        ".mac",
        "aie::accum",
        "acc48",
        "acc80"
    ],
    "mutation_strategy": "Pass an accumulator of incorrect precision to the sliding_mac_ops::mac() call. For example, pass an aie::accum<acc48,8> when the operation requires acc80 due to int32 x int16 multiplication, or vice versa. Alternatively, initialize with aie::zeros<acc48,8>() when acc80 is needed.",
    "repair_expectation": "Change the accumulator type to match the required precision for the data types used in the sliding_mac operation (e.g., acc48 for int16 x int16, acc80 for int32 x int16).",
    "validation_signal": "WSL Vitis/AIE compile failure with type conversion error or template argument deduction failure for accumulator type.",
    "tags": ["acc48", "acc80", "accumulator", "sliding_mac", "sliding_mul_and_mac", "type_error"]
}

import re
import copy


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    return any(path.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def _swap_acc_type(acc_type):
    """Swap accumulator precision tags to an incompatible width."""
    if acc_type == 'acc48':
        return 'acc80'
    elif acc_type == 'acc80':
        return 'acc48'
    elif acc_type == 'acc64':
        return 'acc48'
    elif acc_type == 'cacc48':
        return 'cacc80'
    elif acc_type == 'cacc80':
        return 'cacc48'
    return None


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern 1: aie::accum<acc48, N> / ::aie::accum<acc64, N> etc.
    accum_pattern = re.compile(
        r'((?:::)?aie::accum\s*<\s*)(acc48|acc64|acc80|cacc48|cacc80)(\s*,\s*\d+\s*>)'
    )

    # Pattern 2: aie::zeros<acc48, N>() / ::aie::zeros<acc64, N>()
    zeros_pattern = re.compile(
        r'((?:::)?aie::zeros\s*<\s*)(acc48|acc64|acc80|cacc48|cacc80)(\s*,\s*\d+\s*>\s*\(\s*\))'
    )

    # Legacy vector accumulator tokens used by older AIE kernels.
    legacy_acc_pattern = re.compile(
        r'\bv(\d+)(c?acc)(48|80)\b'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file is relevant (contains sliding_mac context)
        has_sliding_mac = ('sliding_mac' in content or
                          'sliding_mac_ops' in content or
                          'mac4' in content or
                          ('.mac' in content and any(tag in content for tag in ('acc48', 'acc64', 'acc80'))))

        if not has_sliding_mac:
            continue

        # Find aie::accum<accXX, N> candidates
        for m in accum_pattern.finditer(content):
            original_acc = m.group(2)
            swapped = _swap_acc_type(original_acc)
            if swapped is None:
                continue

            original_text = m.group(0)
            replacement_text = m.group(1) + swapped + m.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mac_accumulator_type_wrong",
                "category": "sliding_mul_and_mac",
                "start": m.start(),
                "end": m.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed accumulator type from {original_acc} to {swapped} "
                    f"in aie::accum declaration, causing type mismatch for sliding_mac operation."
                )
            })

        # Find aie::zeros<accXX, N>() candidates
        for m in zeros_pattern.finditer(content):
            original_acc = m.group(2)
            swapped = _swap_acc_type(original_acc)
            if swapped is None:
                continue

            original_text = m.group(0)
            replacement_text = m.group(1) + swapped + m.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mac_accumulator_type_wrong",
                "category": "sliding_mul_and_mac",
                "start": m.start(),
                "end": m.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed accumulator type from {original_acc} to {swapped} "
                    f"in aie::zeros initialization, causing type mismatch for sliding_mac operation."
                )
            })

        for m in legacy_acc_pattern.finditer(content):
            lane_count, prefix, width = m.groups()
            original_acc = f"{prefix}{width}"
            swapped_acc = _swap_acc_type(original_acc)
            if swapped_acc is None:
                continue
            replacement_text = f"v{lane_count}{swapped_acc}"
            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mac_accumulator_type_wrong",
                "category": "sliding_mul_and_mac",
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": replacement_text,
                "description": (
                    f"Changed legacy accumulator vector token from {m.group(0)} "
                    f"to {replacement_text}, causing a sliding MAC precision mismatch."
                )
            })

    # If no accum/zeros patterns found, try a simpler approach: swap standalone acc48/acc80
    # only in lines that also mention sliding_mac or .mac
    if not candidates:
        standalone_pattern = re.compile(r'\b(acc48|acc64|acc80|cacc48|cacc80)\b')
        for file_path, content in project_files.items():
            if not _is_kernel_source(file_path):
                continue
            if 'sliding_mac' not in content and '.mac' not in content and 'mac4' not in content:
                continue

            lines = content.split('\n')
            offset = 0
            for line in lines:
                # Only mutate lines with relevant context
                if ('accum' in line or 'zeros' in line or 'acc' in line) and \
                   ('sliding_mac' in line or '.mac' in line or 'accum' in line or 'zeros' in line):
                    for m in standalone_pattern.finditer(line):
                        original_acc = m.group(1)
                        swapped = _swap_acc_type(original_acc)
                        if swapped is None:
                            continue

                        abs_start = offset + m.start()
                        abs_end = offset + m.end()

                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "sliding_mac_accumulator_type_wrong",
                            "category": "sliding_mul_and_mac",
                            "start": abs_start,
                            "end": abs_end,
                            "original": original_acc,
                            "replacement": swapped,
                            "description": (
                                f"Changed accumulator precision from {original_acc} to {swapped} "
                                f"causing type mismatch for sliding_mac operation."
                            )
                        })
                offset += len(line) + 1  # +1 for newline

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
