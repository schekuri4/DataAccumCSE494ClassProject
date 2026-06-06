import re
import copy

BUG_FAMILY = {
    "family_id": "BF251",
    "bug_type": "mul_operand_type_mismatch",
    "category": "arithmetic_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::mul",
        "aie::vector<int16",
        "aie::vector<int32",
        "aie::vector<cint16"
    ],
    "mutation_strategy": "Replace one operand of aie::mul with a vector of incompatible element type (e.g., multiply int16 vector by cint32 vector where no such overload exists), or mix float and integer vector operands in aie::mul.",
    "repair_expectation": "Change the mismatched operand type to a supported combination (e.g., both int16, or int16 x int32 where supported) so the overload resolves.",
    "validation_signal": "WSL Vitis/AIE aiecompiler emits a compile-time error about no matching overload for aie::mul or template substitution failure.",
    "tags": [
        "arithmetic_intrinsics",
        "mul",
        "overload_resolution",
        "type_mismatch",
        "vector_intrinsics"
    ]
}

# Incompatible type replacements for mutation
_INCOMPATIBLE_REPLACEMENTS = {
    "int16": "cint32",
    "int32": "float",
    "cint16": "float",
    "cint32": "int16",
    "float": "int16",
    "int8": "cint32",
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, or .h files."""
    return path.endswith(('.cpp', '.cc', '.h', '.hpp'))


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to find aie::mul calls with vector operands
    # We look for aie::mul( ... ) calls
    mul_call_pattern = re.compile(r'aie::mul\s*\(([^)]+)\)')

    # Pattern to find vector declarations that could be operands
    # e.g., aie::vector<int16, 32> or aie::vector<int32, 16>
    vector_decl_pattern = re.compile(
        r'aie::vector\s*<\s*(int8|int16|int32|cint16|cint32|float)\s*,\s*(\d+)\s*>'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find aie::mul calls and mutate operand type in variable declarations
        # that feed into aie::mul

        # Find all aie::mul calls
        for mul_match in mul_call_pattern.finditer(content):
            args_str = mul_match.group(1)
            # Try to identify variable names used as arguments
            arg_names = [a.strip() for a in args_str.split(',')]

            # For each argument, look for its declaration as aie::vector<type, N>
            for arg_name in arg_names:
                # Search for declaration of this variable
                # Pattern: aie::vector<type, N> var_name or auto var_name = ...aie::vector<type,N>...
                decl_pattern = re.compile(
                    r'(aie::vector\s*<\s*)(int8|int16|int32|cint16|cint32|float)(\s*,\s*\d+\s*>\s*(?:&\s*)?)' +
                    re.escape(arg_name) + r'\b'
                )
                for decl_match in decl_pattern.finditer(content):
                    original_type = decl_match.group(2)
                    if original_type in _INCOMPATIBLE_REPLACEMENTS:
                        replacement_type = _INCOMPATIBLE_REPLACEMENTS[original_type]
                        original_text = decl_match.group(0)
                        replacement_text = decl_match.group(1) + replacement_type + decl_match.group(3) + arg_name

                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "mul_operand_type_mismatch",
                            "category": "arithmetic_intrinsics",
                            "start": decl_match.start(),
                            "end": decl_match.end(),
                            "original": original_text,
                            "replacement": replacement_text,
                            "description": (
                                f"Changed vector element type from '{original_type}' to '{replacement_type}' "
                                f"for variable '{arg_name}' used in aie::mul, creating a type mismatch."
                            )
                        })

        # Strategy 2: Directly mutate vector type in aie::mul arguments if they are
        # inline expressions like aie::mul(vec_a.cast_to<aie::vector<int16,32>>(), ...)
        # or if the mul call contains explicit vector type casts
        inline_vec_in_mul = re.compile(
            r'(aie::mul\s*\([^)]*aie::vector\s*<\s*)(int8|int16|int32|cint16|cint32|float)(\s*,\s*\d+\s*>)'
        )
        for m in inline_vec_in_mul.finditer(content):
            original_type = m.group(2)
            if original_type in _INCOMPATIBLE_REPLACEMENTS:
                replacement_type = _INCOMPATIBLE_REPLACEMENTS[original_type]
                original_text = m.group(0)
                replacement_text = m.group(1) + replacement_type + m.group(3)

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "mul_operand_type_mismatch",
                    "category": "arithmetic_intrinsics",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Changed inline vector element type from '{original_type}' to '{replacement_type}' "
                        f"inside aie::mul call, creating a type mismatch."
                    )
                })

        # Strategy 3: Find any aie::vector declaration with matching types near aie::mul usage
        # More aggressive: find all vector declarations with target types in files containing aie::mul
        if 'aie::mul' in content:
            for vec_match in vector_decl_pattern.finditer(content):
                original_type = vec_match.group(1)
                if original_type not in _INCOMPATIBLE_REPLACEMENTS:
                    continue

                # Check if this declaration is likely used in a mul (within ~10 lines)
                line_start = content.rfind('\n', 0, vec_match.start()) + 1
                # Look ahead ~500 chars for aie::mul usage
                context_after = content[vec_match.end():vec_match.end() + 500]
                if 'aie::mul' not in context_after:
                    # Also check if aie::mul appears before (variable declared then used)
                    context_before = content[max(0, vec_match.start() - 500):vec_match.start()]
                    # Skip if no mul nearby at all - but be lenient
                    pass

                replacement_type = _INCOMPATIBLE_REPLACEMENTS[original_type]
                original_text = vec_match.group(0)
                replacement_text = 'aie::vector<' + replacement_type + ', ' + vec_match.group(2) + '>'

                # Avoid duplicates with strategy 1
                already_found = any(
                    c["file_path"] == file_path and c["start"] == vec_match.start()
                    for c in candidates
                )
                if not already_found:
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "mul_operand_type_mismatch",
                        "category": "arithmetic_intrinsics",
                        "start": vec_match.start(),
                        "end": vec_match.end(),
                        "original": original_text,
                        "replacement": replacement_text,
                        "description": (
                            f"Changed vector element type from '{original_type}' to '{replacement_type}' "
                            f"in a file using aie::mul, creating a potential operand type mismatch."
                        )
                    })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    new_files = dict(project_files)
    content = new_files[file_path]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
