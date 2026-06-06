import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF254",
    "bug_type": "shift_unsupported_element_type",
    "category": "arithmetic_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::upshift",
        "aie::downshift",
        "srs",
        "ups",
        "aie::vector<float"
    ],
    "mutation_strategy": "Apply aie::upshift or aie::downshift to a floating-point vector (e.g., aie::vector<float,8>) where only integer types are supported, or use an invalid shift amount type (e.g., a vector instead of scalar).",
    "repair_expectation": "Change the vector element type to an integer type (int16, int32, etc.) or use the correct scalar shift amount parameter.",
    "validation_signal": "WSL Vitis/AIE aiecompiler emits a compile-time error about no matching overload for shift operations on float types.",
    "tags": [
        "arithmetic_intrinsics",
        "downshift",
        "float",
        "shift",
        "unsupported_type",
        "upshift"
    ]
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: kernel source files are .cc, .cpp, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp', '.hxx'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find aie::upshift or aie::downshift applied to integer vectors
        # and mutate the vector type to float
        # Look for patterns like aie::vector<int32, N> or aie::vector<int16_t, N> etc.
        int_vector_pattern = re.compile(
            r'(aie::vector<\s*)(int\d+_t|int\d+|uint\d+_t|uint\d+|int|unsigned int|short|unsigned short|int8|int16|int32|uint8|uint16|uint32)(\s*,\s*\d+\s*>)'
        )

        # Check if the file uses shift operations
        has_shift_ops = bool(re.search(r'aie::(upshift|downshift)|(?<!\w)(srs|ups)\s*\(', content))

        if has_shift_ops:
            for match in int_vector_pattern.finditer(content):
                original = match.group(0)
                prefix = match.group(1)
                suffix = match.group(3)
                replacement = prefix + "float" + suffix

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shift_unsupported_element_type",
                    "category": "arithmetic_intrinsics",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": f"Changed integer vector type to float in shift operation context: '{original}' -> '{replacement}'"
                })

        # Strategy 2: Find shift operations on integer vectors and replace the call
        # to use a float vector directly by injecting aie::upshift on a float vector
        # Look for aie::upshift(...) or aie::downshift(...) calls and change the argument type
        shift_call_pattern = re.compile(
            r'(aie::(upshift|downshift)\s*\(\s*)(\w+)(\s*,\s*\w+\s*\))'
        )

        for match in shift_call_pattern.finditer(content):
            # We can mutate the shift amount to be a vector expression instead of scalar
            original = match.group(0)
            prefix = match.group(1)
            var_name = match.group(3)
            suffix = match.group(4)

            # Mutation: wrap the shift amount in a vector constructor to cause type error
            # Replace scalar shift amount with a vector
            inner_match = re.search(r',\s*(\w+)\s*\)', original)
            if inner_match:
                shift_arg = inner_match.group(1)
                new_shift_arg = f"aie::broadcast<float, 8>({shift_arg})"
                replacement = original[:inner_match.start(1)] + new_shift_arg + original[inner_match.end(1):]

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shift_unsupported_element_type",
                    "category": "arithmetic_intrinsics",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": f"Replaced scalar shift amount with vector broadcast of float: '{original}' -> '{replacement}'"
                })

        # Strategy 3: If file has no shift ops but has integer vectors, inject a shift on float
        if not has_shift_ops and not candidates:
            # Look for any aie::vector declaration we can add a shift after
            vec_decl_pattern = re.compile(
                r'(aie::vector<\s*(?:int\d+_t|int\d+|uint\d+_t|uint\d+)\s*,\s*(\d+)\s*>\s+(\w+)\s*[=;].*?)(\n)'
            )
            for match in vec_decl_pattern.finditer(content):
                vec_size = match.group(2)
                vec_name = match.group(3)
                original = match.group(0)
                # Insert a line that applies upshift to a float vector
                injected_line = f"\n    aie::vector<float, {vec_size}> float_shifted = aie::upshift(aie::broadcast<float, {vec_size}>(1.0f), 4);"
                replacement = original + injected_line

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shift_unsupported_element_type",
                    "category": "arithmetic_intrinsics",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": f"Injected aie::upshift on float vector after integer vector declaration of '{vec_name}'"
                })
                break  # One injection is enough

        # Strategy 4: If file has srs/ups calls, mutate the accumulator/vector type to float
        srs_ups_pattern = re.compile(r'((?:srs|ups)\s*\(\s*)(\w+)(\s*,\s*\d+\s*\))')
        for match in srs_ups_pattern.finditer(content):
            original = match.group(0)
            func_and_open = match.group(1)
            arg = match.group(2)
            rest = match.group(3)
            # Cast the argument to a float vector
            replacement = func_and_open + f"aie::vector<float, 8>()" + rest

            candidates.append({
                "file_path": file_path,
                "bug_type": "shift_unsupported_element_type",
                "category": "arithmetic_intrinsics",
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": f"Replaced srs/ups argument with float vector: '{original}' -> '{replacement}'"
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
