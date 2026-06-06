import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF124",
    "bug_type": "window_read_api_type_mismatch",
    "category": "window_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "window_readincr",
        "window_read",
        "window_readincr_v<",
        "aie::vector"
    ],
    "mutation_strategy": "Use window_readincr or window_readincr_v with a vector lane count or type that does not match the window's declared element type (e.g., call window_readincr_v<8>(win) on an input_window<cint16> but assign to aie::vector<int32,8>), creating a type or lane mismatch at the read API call site.",
    "repair_expectation": "Match the template parameter type and lane count of the read API to the window's element type and a valid vector width for that type.",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or type conversion failure in kernel source.",
    "tags": [
        "compile_time",
        "kernel",
        "lane_mismatch",
        "type_mismatch",
        "window_interfaces",
        "window_readincr"
    ]
}

# Type mismatch mappings: original type -> mismatched type
_TYPE_MISMATCH = {
    "int16": "int32",
    "int32": "int16",
    "cint16": "int32",
    "cint32": "cint16",
    "float": "int32",
    "int8": "int16",
    "uint8": "int16",
    "uint16": "int32",
    "uint32": "int16",
}

# Lane count mismatch: original -> mismatched
_LANE_MISMATCH = {
    "4": "8",
    "8": "16",
    "16": "8",
    "32": "16",
    "2": "4",
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: kernel source files are .cpp, .cc, or .h files likely containing AIE kernel code."""
    return path.endswith(('.cpp', '.cc', '.h', '.hpp'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find aie::vector<TYPE, LANES> assignments from window_readincr_v or window_readincr
        # Pattern: aie::vector<type, lanes> var = window_readincr_v<lanes>(win);
        # We mutate the aie::vector type to create a mismatch

        # Pattern for aie::vector declarations that are assigned from window_readincr_v
        pattern_vec_assign = re.compile(
            r'(aie::vector\s*<\s*)(\w+)(\s*,\s*)(\d+)(\s*>\s*\w+\s*=\s*window_readincr_v\s*<\s*)(\d+)(\s*>\s*\([^)]*\))'
        )
        for m in pattern_vec_assign.finditer(content):
            orig_type = m.group(2)
            orig_lanes = m.group(4)
            read_lanes = m.group(6)

            # Create type mismatch in the vector declaration
            if orig_type in _TYPE_MISMATCH:
                new_type = _TYPE_MISMATCH[orig_type]
                original_text = m.group(0)
                replacement_text = m.group(1) + new_type + m.group(3) + orig_lanes + m.group(5) + read_lanes + m.group(7)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "window_read_api_type_mismatch",
                    "category": "window_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": f"Changed aie::vector element type from '{orig_type}' to '{new_type}' creating type mismatch with window_readincr_v call."
                })

            # Create lane mismatch
            if orig_lanes in _LANE_MISMATCH:
                new_lanes = _LANE_MISMATCH[orig_lanes]
                original_text = m.group(0)
                replacement_text = m.group(1) + orig_type + m.group(3) + new_lanes + m.group(5) + read_lanes + m.group(7)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "window_read_api_type_mismatch",
                    "category": "window_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": f"Changed aie::vector lane count from {orig_lanes} to {new_lanes} creating lane mismatch with window_readincr_v<{read_lanes}> call."
                })

        # Strategy 2: Find window_readincr_v<LANES>(win) and change the lanes
        pattern_readincr_v = re.compile(
            r'(window_readincr_v\s*<\s*)(\d+)(\s*>\s*\([^)]*\))'
        )
        for m in pattern_readincr_v.finditer(content):
            orig_lanes = m.group(2)
            if orig_lanes in _LANE_MISMATCH:
                new_lanes = _LANE_MISMATCH[orig_lanes]
                original_text = m.group(0)
                replacement_text = m.group(1) + new_lanes + m.group(3)
                # Avoid duplicates with strategy 1 by checking if this is a standalone mutation
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "window_read_api_type_mismatch",
                    "category": "window_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": f"Changed window_readincr_v lane count from {orig_lanes} to {new_lanes} creating lane mismatch with receiving vector type."
                })

        # Strategy 3: Find window_readincr(win) calls and replace with window_readincr_v<WRONG_LANES>(win)
        pattern_readincr = re.compile(
            r'(window_readincr\s*\(\s*)(\w+)(\s*\))'
        )
        for m in pattern_readincr.finditer(content):
            # Check it's not already window_readincr_v
            prefix_check = content[max(0, m.start()-2):m.start()]
            if 'v' in prefix_check or '_v' in content[m.start()-2:m.start()]:
                continue
            original_text = m.group(0)
            win_name = m.group(2)
            replacement_text = f"window_readincr_v<7>({win_name})"
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_read_api_type_mismatch",
                "category": "window_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": f"Replaced window_readincr({win_name}) with window_readincr_v<7>({win_name}) creating a lane/type mismatch."
            })

        # Strategy 4: Find window_read(win) and mutate to window_readincr_v with wrong type
        pattern_read = re.compile(
            r'(window_read\s*\(\s*)(\w+)(\s*\))'
        )
        for m in pattern_read.finditer(content):
            # Avoid matching window_readincr
            before = content[max(0, m.start()-4):m.start()]
            if 'incr' in before:
                continue
            original_text = m.group(0)
            win_name = m.group(2)
            replacement_text = f"window_readincr_v<7>({win_name})"
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_read_api_type_mismatch",
                "category": "window_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": f"Replaced window_read({win_name}) with window_readincr_v<7>({win_name}) creating a type/lane mismatch."
            })

        # Strategy 5: Find aie::vector<TYPE, LANES> with nearby window_readincr and mutate the vector type
        pattern_vec_decl = re.compile(
            r'(aie::vector\s*<\s*)(\w+)(\s*,\s*)(\d+)(\s*>)'
        )
        for m in pattern_vec_decl.finditer(content):
            # Check if there's a window_readincr nearby (within 200 chars after)
            context_after = content[m.end():m.end()+200]
            if 'window_readincr' not in context_after and 'window_read' not in context_after:
                # Also check if assignment is on same line
                line_end = content.find('\n', m.end())
                if line_end == -1:
                    line_end = len(content)
                line_content = content[m.end():line_end]
                if 'window_readincr' not in line_content and 'window_read' not in line_content:
                    continue

            orig_type = m.group(2)
            orig_lanes = m.group(4)

            # Skip if already covered by strategy 1
            # Check if this exact position was already matched
            already_covered = any(
                c["file_path"] == file_path and c["start"] <= m.start() and c["end"] >= m.end()
                for c in candidates
            )
            if already_covered:
                continue

            if orig_type in _TYPE_MISMATCH:
                new_type = _TYPE_MISMATCH[orig_type]
                original_text = m.group(0)
                replacement_text = m.group(1) + new_type + m.group(3) + orig_lanes + m.group(5)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "window_read_api_type_mismatch",
                    "category": "window_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": f"Changed aie::vector element type from '{orig_type}' to '{new_type}' near window read call, creating type mismatch."
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy
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
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
