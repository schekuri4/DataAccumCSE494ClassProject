import re
import copy

BUG_FAMILY = {
    "family_id": "BF234",
    "bug_type": "accum_broadcast_invalid_type",
    "category": "accumulator_types",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::broadcast",
        "aie::accum",
        ".from_vector(",
        "acc48",
        "acc80"
    ],
    "mutation_strategy": "Attempt to initialize an accumulator using aie::broadcast with a scalar type incompatible with the accumulator (e.g., broadcasting a float into an acc48 integer accumulator, or using a vector broadcast where an accumulator-specific initialization is required).",
    "repair_expectation": "Use the correct initialization method such as aie::zeros, from_vector with proper shift, or broadcast with a compatible scalar type.",
    "validation_signal": "WSL Vitis/AIE compile failure with type conversion error or no viable overload for broadcast-to-accumulator assignment.",
    "tags": [
        "accum_assign",
        "accumulator_types",
        "broadcast",
        "initialization",
        "type_incompatible"
    ]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    return any(path.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        lines = content.split('\n')

        # Strategy 1: Find accumulator declarations/assignments using from_vector or aie::zeros
        # and replace with aie::broadcast using incompatible type
        # Pattern: something = aie::zeros<acc48, N>() or .from_vector<acc48>(vec, shift)
        # Mutate to: something = aie::broadcast<float, N>(1.0f)

        # Look for aie::zeros<accXX, N>() patterns
        zeros_pattern = re.compile(
            r'(aie::zeros\s*<\s*(acc48|acc80)\s*,\s*(\d+)\s*>\s*\(\s*\))'
        )
        for line_idx, line in enumerate(lines):
            for m in zeros_pattern.finditer(line):
                original = m.group(1)
                acc_type = m.group(2)
                lanes = m.group(3)
                # Replace with broadcast of incompatible float type
                replacement = f'aie::broadcast<float, {lanes}>(1.0f)'
                start_col = m.start(1)
                end_col = m.end(1)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "accum_broadcast_invalid_type",
                    "category": "accumulator_types",
                    "start": {"line": line_idx, "col": start_col},
                    "end": {"line": line_idx, "col": end_col},
                    "original": original,
                    "replacement": replacement,
                    "description": f"Replace {acc_type} zero-initialization with aie::broadcast<float> (incompatible type for integer accumulator)"
                })

        # Look for .from_vector<accXX>(vec, shift) patterns
        from_vec_pattern = re.compile(
            r'(\.from_vector\s*<\s*(acc48|acc80)\s*>\s*\([^)]+\))'
        )
        for line_idx, line in enumerate(lines):
            for m in from_vec_pattern.finditer(line):
                original = m.group(1)
                acc_type = m.group(2)
                replacement = ' = aie::broadcast<float, 8>(0.5f)'
                # We replace the from_vector call with an assignment via broadcast
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "accum_broadcast_invalid_type",
                    "category": "accumulator_types",
                    "start": {"line": line_idx, "col": m.start(1)},
                    "end": {"line": line_idx, "col": m.end(1)},
                    "original": original,
                    "replacement": replacement,
                    "description": f"Replace from_vector<{acc_type}> initialization with aie::broadcast<float> (type mismatch)"
                })

        # Look for aie::accum<acc48|acc80, N> variable declarations with initialization
        accum_decl_pattern = re.compile(
            r'(aie::accum\s*<\s*(acc48|acc80)\s*,\s*(\d+)\s*>\s+(\w+)\s*=\s*)([^;]+)(;)'
        )
        for line_idx, line in enumerate(lines):
            for m in accum_decl_pattern.finditer(line):
                acc_type = m.group(2)
                lanes = m.group(3)
                var_name = m.group(4)
                init_expr = m.group(5)
                # Only mutate if init is not already a broadcast<float>
                if 'broadcast<float' in init_expr:
                    continue
                original = init_expr
                replacement = f'aie::broadcast<float, {lanes}>(1.0f)'
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "accum_broadcast_invalid_type",
                    "category": "accumulator_types",
                    "start": {"line": line_idx, "col": m.start(5)},
                    "end": {"line": line_idx, "col": m.end(5)},
                    "original": original,
                    "replacement": replacement,
                    "description": f"Replace {acc_type} accumulator initialization with aie::broadcast<float> (incompatible scalar type for integer accumulator)"
                })

        # Look for aie::broadcast<int..> assigned to accum and flip to float
        broadcast_pattern = re.compile(
            r'(aie::broadcast\s*<\s*(int\d+|int8|int16|int32)\s*,\s*(\d+)\s*>\s*\([^)]*\))'
        )
        for line_idx, line in enumerate(lines):
            # Only consider lines that also reference acc48 or acc80
            if 'acc48' not in line and 'acc80' not in line:
                continue
            for m in broadcast_pattern.finditer(line):
                original = m.group(1)
                lanes = m.group(3)
                replacement = f'aie::broadcast<float, {lanes}>(1.0f)'
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "accum_broadcast_invalid_type",
                    "category": "accumulator_types",
                    "start": {"line": line_idx, "col": m.start(1)},
                    "end": {"line": line_idx, "col": m.end(1)},
                    "original": original,
                    "replacement": replacement,
                    "description": "Replace integer broadcast with float broadcast (incompatible with integer accumulator)"
                })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    if file_path not in project_files:
        return dict(project_files)

    content = project_files[file_path]
    lines = content.split('\n')

    line_idx = candidate["start"]["line"]
    col_start = candidate["start"]["col"]
    col_end = candidate["end"]["col"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    line = lines[line_idx]

    # Verify the original text is at the expected position
    if line[col_start:col_end] == original:
        new_line = line[:col_start] + replacement + line[col_end:]
    else:
        # Fallback: replace first occurrence in the line
        new_line = line.replace(original, replacement, 1)

    lines[line_idx] = new_line

    new_content = '\n'.join(lines)
    result = dict(project_files)
    result[file_path] = new_content
    return result
