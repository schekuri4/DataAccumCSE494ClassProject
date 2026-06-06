import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF125",
    "bug_type": "window_writeincr_wrong_vector_lanes",
    "category": "window_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "window_writeincr",
        "window_writeincr_v",
        "output_window<",
        "aie::vector<"
    ],
    "mutation_strategy": "Call window_writeincr or window_writeincr_v with a vector whose lane count is not supported for the output_window element type on AIE (e.g., writing a v16int32 into an output_window<int32> using window_writeincr_v<16> when only 4 or 8 lanes are valid for the API variant).",
    "repair_expectation": "Use a supported vector lane count for the given element type in the writeincr call.",
    "validation_signal": "WSL Vitis/AIE compile failure with no matching function or invalid template argument for window_writeincr_v.",
    "tags": [
        "compile_time",
        "kernel",
        "lane_count",
        "vector",
        "window_interfaces",
        "window_writeincr"
    ]
}

# Supported lane counts for common AIE element types in window_writeincr
# We'll use these to pick an *unsupported* lane count for mutation
_VALID_LANES = {
    "int8": [16, 32, 64, 128],
    "int16": [8, 16, 32],
    "int32": [4, 8, 16],
    "cint16": [4, 8, 16],
    "cint32": [2, 4, 8],
    "float": [4, 8, 16],
    "cfloat": [2, 4, 8],
    "uint8": [16, 32, 64, 128],
    "uint16": [8, 16, 32],
    "uint32": [4, 8, 16],
}

# For a given valid lane count, pick an invalid one
def _pick_invalid_lanes(elem_type: str, current_lanes: int) -> int:
    valid = _VALID_LANES.get(elem_type, [4, 8])
    # Try common invalid choices
    candidates = [16, 32, 64, 128, 2, 3, 5, 7, 6]
    for c in candidates:
        if c not in valid and c != current_lanes:
            return c
    # Fallback: double the largest valid
    if valid:
        doubled = max(valid) * 2
        if doubled != current_lanes:
            return doubled
    return 99  # clearly invalid


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    # Pattern 1: window_writeincr_v<N>(window_ptr, vector_expr)
    # We look for the template argument N and change it to an unsupported value
    pat_writeincr_v = re.compile(
        r'(window_writeincr_v\s*<\s*)(\d+)(\s*>\s*\()'
    )

    # Pattern 2: window_writeincr(window_ptr, vector_expr) where vector is aie::vector<type, N>
    # We look for the vector declaration/definition near the writeincr call
    pat_writeincr = re.compile(
        r'(window_writeincr\s*\(\s*\w+\s*,\s*)(\w+)'
    )

    # Pattern 3: aie::vector<type, N> var declarations that are used with window_writeincr
    pat_vector_decl = re.compile(
        r'(aie::vector\s*<\s*(\w+)\s*,\s*)(\d+)(\s*>)'
    )

    # Pattern for output_window<type> to determine element type
    pat_output_window = re.compile(
        r'output_window\s*<\s*(\w+)\s*>'
    )

    for file_path, content in project_files.items():
        # Only consider kernel source files (common extensions)
        if not any(file_path.endswith(ext) for ext in ['.cc', '.cpp', '.c', '.h', '.hpp']):
            continue

        # Check if file has any relevant content
        has_writeincr = 'window_writeincr' in content
        if not has_writeincr:
            continue

        # Determine element type from output_window declaration in this file
        elem_type_match = pat_output_window.search(content)
        elem_type = elem_type_match.group(1) if elem_type_match else "int32"

        # Strategy A: Mutate window_writeincr_v<N> template argument
        for m in pat_writeincr_v.finditer(content):
            original_lanes = int(m.group(2))
            invalid_lanes = _pick_invalid_lanes(elem_type, original_lanes)
            
            original_text = m.group(0)
            replacement_text = m.group(1) + str(invalid_lanes) + m.group(3)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_writeincr_wrong_vector_lanes",
                "category": "window_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed window_writeincr_v lane count from {original_lanes} to "
                    f"{invalid_lanes} (unsupported for {elem_type}) in {file_path}"
                )
            })

        # Strategy B: Mutate aie::vector<type, N> declarations that feed into window_writeincr
        for m in pat_vector_decl.finditer(content):
            vec_elem_type = m.group(2)
            original_lanes = int(m.group(3))
            
            # Check if this vector variable is likely used with window_writeincr
            # Look for the variable name after the declaration
            after_decl = content[m.end():]
            var_name_match = re.match(r'\s*(\w+)', after_decl)
            if not var_name_match:
                continue
            var_name = var_name_match.group(1)
            
            # Check if var_name appears in a window_writeincr call
            writeincr_usage = re.search(
                r'window_writeincr\w*\s*(?:<[^>]*>\s*)?\([^,]*,\s*' + re.escape(var_name),
                content
            )
            if not writeincr_usage:
                continue

            invalid_lanes = _pick_invalid_lanes(vec_elem_type, original_lanes)
            
            original_text = m.group(0)
            replacement_text = m.group(1) + str(invalid_lanes) + m.group(4)
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "window_writeincr_wrong_vector_lanes",
                "category": "window_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed aie::vector<{vec_elem_type}, {original_lanes}> to "
                    f"aie::vector<{vec_elem_type}, {invalid_lanes}> (unsupported lane count "
                    f"for window_writeincr) in {file_path}"
                )
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
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
