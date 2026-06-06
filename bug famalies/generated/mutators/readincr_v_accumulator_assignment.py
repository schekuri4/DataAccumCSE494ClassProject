import re
import copy

BUG_FAMILY = {
    "family_id": "BF118",
    "bug_type": "readincr_v_accumulator_assignment",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<",
        "aie::accum<",
        "acc48",
        "acc80",
        "aie::vector<"
    ],
    "mutation_strategy": "Assign the result of readincr_v<N> (which returns aie::vector) directly to an aie::accum variable without proper conversion, or attempt to use readincr_v to populate an accumulator type, confusing vector and accumulator APIs.",
    "repair_expectation": "Use proper conversion from vector to accumulator (e.g., aie::from_vector<acc48>) or assign readincr_v result to a vector variable first, then convert.",
    "validation_signal": "WSL Vitis/AIE compile failure with no viable conversion from aie::vector to aie::accum or type mismatch in assignment.",
    "tags": ["acc48", "accumulator", "readincr_v", "stream_vector_interfaces", "type_conversion"]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, or .h files likely containing AIE kernel code."""
    return path.endswith(('.cpp', '.cc', '.h', '.hpp'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find assignments like `aie::vector<type, N> var = readincr_v<N>(stream);`
        # and mutate them to `aie::accum<acc48, N> var = readincr_v<N>(stream);`
        # Pattern: aie::vector<TYPE, N> VARNAME = readincr_v<...>(...)
        pattern1 = re.compile(
            r'(aie::vector<\s*(\w+)\s*,\s*(\d+)\s*>\s+(\w+)\s*=\s*(readincr_v<\s*\d+\s*>\s*\([^)]*\))\s*;)'
        )
        for m in pattern1.finditer(content):
            original = m.group(1)
            elem_type = m.group(2)
            vec_size = m.group(3)
            var_name = m.group(4)
            readincr_call = m.group(5)

            # Replace aie::vector<type, N> with aie::accum<acc48, N>
            replacement = f'aie::accum<acc48, {vec_size}> {var_name} = {readincr_call};'

            candidates.append({
                "file_path": file_path,
                "bug_type": "readincr_v_accumulator_assignment",
                "category": "stream_vector_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Changed vector variable '{var_name}' to aie::accum<acc48, {vec_size}> while still assigning readincr_v result (which returns aie::vector), causing type mismatch."
            })

        # Strategy 2: Find `auto var = readincr_v<N>(stream)` followed by use,
        # or find readincr_v assigned to a vector and mutate the type declaration
        # Pattern: look for variable declarations with readincr_v using auto
        pattern2 = re.compile(
            r'(auto\s+(\w+)\s*=\s*(readincr_v<\s*(\d+)\s*>\s*\([^)]*\))\s*;)'
        )
        for m in pattern2.finditer(content):
            original = m.group(1)
            var_name = m.group(2)
            readincr_call = m.group(3)
            vec_size = m.group(4)

            # Replace auto with explicit aie::accum<acc48, N> - this will fail because
            # readincr_v returns a vector, not an accumulator
            replacement = f'aie::accum<acc48, {vec_size}> {var_name} = {readincr_call};'

            candidates.append({
                "file_path": file_path,
                "bug_type": "readincr_v_accumulator_assignment",
                "category": "stream_vector_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Replaced 'auto' with 'aie::accum<acc48, {vec_size}>' for variable '{var_name}' assigned from readincr_v, causing type mismatch."
            })

        # Strategy 3: Find existing aie::accum declarations that use proper conversion
        # like `aie::accum<acc48, N> var = aie::from_vector<acc48>(readincr_v<N>(stream));`
        # and remove the conversion wrapper
        pattern3 = re.compile(
            r'(aie::accum<\s*(acc\d+)\s*,\s*(\d+)\s*>\s+(\w+)\s*=\s*aie::from_vector<\s*acc\d+\s*>\s*\(\s*(readincr_v<\s*\d+\s*>\s*\([^)]*\))\s*\)\s*;)'
        )
        for m in pattern3.finditer(content):
            original = m.group(1)
            acc_type = m.group(2)
            vec_size = m.group(3)
            var_name = m.group(4)
            readincr_call = m.group(5)

            # Remove the from_vector conversion, directly assign readincr_v to accum
            replacement = f'aie::accum<{acc_type}, {vec_size}> {var_name} = {readincr_call};'

            candidates.append({
                "file_path": file_path,
                "bug_type": "readincr_v_accumulator_assignment",
                "category": "stream_vector_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Removed aie::from_vector<{acc_type}> conversion, directly assigning readincr_v result to aie::accum<{acc_type}, {vec_size}> variable '{var_name}'."
            })

        # Strategy 4: Find readincr_v usage where result is assigned to a vector,
        # then later converted. We can change the vector type to accum in the declaration.
        # Pattern: aie::vector<TYPE, N> var = readincr_v...
        # Also handle cases with template on readincr_v like readincr_v<8>
        pattern4 = re.compile(
            r'(aie::vector<\s*(\w+)\s*,\s*(\d+)\s*>\s+(\w+)\s*=\s*(::)?readincr_v\s*<\s*(\d+)\s*>\s*\([^)]*\)\s*;)'
        )
        for m in pattern4.finditer(content):
            # Avoid duplicates with pattern1
            if m.group(0) in [c["original"] for c in candidates]:
                continue
            original = m.group(0)
            elem_type = m.group(2)
            vec_size = m.group(3)
            var_name = m.group(4)
            ns_prefix = m.group(5) or ""
            readincr_size = m.group(6)

            readincr_expr = original.split('=', 1)[1].strip().rstrip(';').strip()
            replacement = f'aie::accum<acc48, {vec_size}> {var_name} = {readincr_expr};'

            candidates.append({
                "file_path": file_path,
                "bug_type": "readincr_v_accumulator_assignment",
                "category": "stream_vector_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Changed aie::vector<{elem_type}, {vec_size}> to aie::accum<acc48, {vec_size}> for variable '{var_name}' assigned from readincr_v, causing type mismatch."
            })

        # Strategy 5: If file contains both readincr_v and accum usage but no direct
        # assignment pattern found above, look for readincr_v calls in any context
        # and try to wrap them in an accum assignment
        if not any(c["file_path"] == file_path for c in candidates):
            # Look for standalone readincr_v calls assigned to any variable
            pattern5 = re.compile(
                r'(\w[\w:]*(?:<[^>]*>)?\s+(\w+)\s*=\s*((?:::)?readincr_v\s*<\s*(\d+)\s*>\s*\([^)]*\))\s*;)'
            )
            for m in pattern5.finditer(content):
                original = m.group(1)
                var_name = m.group(2)
                readincr_call = m.group(3)
                vec_size = m.group(4)

                # Only mutate if not already an accum type
                if 'accum' in original:
                    continue

                replacement = f'aie::accum<acc48, {vec_size}> {var_name} = {readincr_call};'

                if replacement != original:
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "readincr_v_accumulator_assignment",
                        "category": "stream_vector_interfaces",
                        "start": m.start(),
                        "end": m.end(),
                        "original": original,
                        "replacement": replacement,
                        "description": f"Changed type of '{var_name}' to aie::accum<acc48, {vec_size}> while assigning readincr_v result, causing type mismatch."
                    })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
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
