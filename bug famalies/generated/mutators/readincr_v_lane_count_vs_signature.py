import re
import copy

BUG_FAMILY = {
    "family_id": "BF037",
    "bug_type": "readincr_v_lane_count_vs_signature",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<",
        "writeincr_v<",
        "readincr_v8(",
        "readincr_v16(",
        "input_stream<int32>"
    ],
    "mutation_strategy": "Change the lane count template parameter in readincr_v<N> or writeincr_v<N> to a value incompatible with the stream element type declared in the kernel signature. For example, use readincr_v<16>(in) on an input_stream<int32>* where only readincr_v<4> or readincr_v<8> are valid for the architecture.",
    "repair_expectation": "Correct the lane count in readincr_v/writeincr_v to match a valid vector size for the stream's element type on the target AIE architecture.",
    "validation_signal": "WSL Vitis/AIE compile failure with invalid vector size or no matching overload for readincr_v/writeincr_v.",
    "tags": [
        "kernel_prototypes_and_signatures",
        "lane_mismatch",
        "readincr_v",
        "stream_intrinsic",
        "writeincr_v"
    ]
}

# Valid lane counts for common AIE element types (used to pick an *invalid* replacement)
_VALID_LANES = {
    "int8": [16, 32, 64],
    "int16": [8, 16, 32],
    "int32": [4, 8, 16],
    "int64": [2, 4, 8],
    "uint8": [16, 32, 64],
    "uint16": [8, 16, 32],
    "uint32": [4, 8, 16],
    "uint64": [2, 4, 8],
    "float": [4, 8, 16],
    "cint16": [4, 8, 16],
    "cint32": [2, 4, 8],
    "cfloat": [2, 4, 8],
}

# All possible lane counts we might substitute
_ALL_LANES = [2, 4, 8, 16, 32, 64]


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cc, .cpp, .c, .h, .hpp in typical AIE projects."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp'))


def _pick_invalid_lane(current_lane, element_type):
    """Pick a lane count that is invalid for the given element type."""
    valid = _VALID_LANES.get(element_type, [4, 8])
    # Candidates: all lanes except current and except valid ones
    candidates = [l for l in _ALL_LANES if l not in valid and l != current_lane]
    if not candidates:
        # If we can't find one outside valid set, just pick something different from current
        candidates = [l for l in _ALL_LANES if l != current_lane]
    if not candidates:
        return None
    # Deterministic: pick the largest invalid lane count
    return max(candidates)


def _detect_stream_element_type(content):
    """Try to detect the element type from input_stream/output_stream declarations."""
    # Look for input_stream<TYPE> or output_stream<TYPE>
    pattern = re.compile(r'(?:input_stream|output_stream)\s*<\s*(\w+)\s*>')
    matches = pattern.findall(content)
    if matches:
        return matches[0]
    return None


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern for readincr_v<N> or writeincr_v<N> with template parameter
    template_pattern = re.compile(r'((?:readincr_v|writeincr_v)\s*<\s*)(\d+)(\s*>)')
    # Pattern for readincr_vN( shorthand forms
    shorthand_pattern = re.compile(r'((?:readincr_v|writeincr_v))(\d+)(\s*\()')

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        element_type = _detect_stream_element_type(content)
        if element_type is None:
            element_type = "int32"  # default assumption

        # Search for template-style calls: readincr_v<N> / writeincr_v<N>
        for m in template_pattern.finditer(content):
            current_lane = int(m.group(2))
            new_lane = _pick_invalid_lane(current_lane, element_type)
            if new_lane is None:
                continue

            original = m.group(0)
            replacement = m.group(1) + str(new_lane) + m.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed lane count in {m.group(0).strip()} from {current_lane} to {new_lane}, "
                    f"which is incompatible with stream element type '{element_type}' on AIE."
                )
            })

        # Search for shorthand-style calls: readincr_v8( / writeincr_v16(
        for m in shorthand_pattern.finditer(content):
            current_lane = int(m.group(2))
            new_lane = _pick_invalid_lane(current_lane, element_type)
            if new_lane is None:
                continue

            original = m.group(0)
            replacement = m.group(1) + str(new_lane) + m.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed lane count in {original.strip()} from {current_lane} to {new_lane}, "
                    f"which is incompatible with stream element type '{element_type}' on AIE."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    if file_path not in project_files:
        return dict(project_files)

    content = project_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files = dict(project_files)
    new_files[file_path] = new_content
    return new_files
