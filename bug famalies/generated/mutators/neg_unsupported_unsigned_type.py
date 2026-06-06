import re
import copy

BUG_FAMILY = {
    "family_id": "BF255",
    "bug_type": "neg_unsupported_unsigned_type",
    "category": "arithmetic_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::neg",
        "aie::vector<uint8",
        "aie::vector<uint16",
        "aie::vector<uint32"
    ],
    "mutation_strategy": "Apply aie::neg to an unsigned integer vector (uint8, uint16, or uint32) where negation is not supported for unsigned types on AIE architecture.",
    "repair_expectation": "Change the vector element type from unsigned to signed (e.g., uint16 to int16) or cast before negation.",
    "validation_signal": "WSL Vitis/AIE aiecompiler emits a compile-time error about unsupported negation on unsigned types or no matching overload.",
    "tags": [
        "arithmetic_intrinsics",
        "neg",
        "type_error",
        "unsigned",
        "unsupported_overload"
    ]
}


def _is_kernel_source(path):
    """Heuristic: consider .cpp, .cc, .h, .hpp files as potential kernel sources."""
    return any(path.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find aie::neg applied to a signed vector and change the vector type to unsigned
        # Look for patterns like: aie::neg(some_var) where some_var is declared as aie::vector<int8,...> etc.
        # We look for vector declarations with signed types and mutate them to unsigned

        # Find aie::vector declarations with signed integer types
        # Pattern: aie::vector<int8, N> or aie::vector<int16, N> or aie::vector<int32, N>
        vec_decl_pattern = re.compile(
            r'(aie::vector<)(int(?:8|16|32))(,\s*\d+\s*>)'
        )

        # First check if aie::neg is used in this file
        if 'aie::neg' not in content:
            continue

        for match in vec_decl_pattern.finditer(content):
            signed_type = match.group(2)  # e.g., "int16"
            unsigned_type = 'u' + signed_type  # e.g., "uint16"

            original = match.group(0)
            replacement = match.group(1) + unsigned_type + match.group(3)

            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "neg_unsupported_unsigned_type",
                "category": "arithmetic_intrinsics",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Change vector element type from '{signed_type}' to '{unsigned_type}' "
                    f"making aie::neg operate on an unsigned type, which is unsupported on AIE."
                )
            })

        # Strategy 2: If there are already unsigned vectors but no aie::neg, 
        # or if we find a signed neg call and can change the type inline
        # Look for aie::neg(expr) where we can wrap or change the argument type
        # Find patterns like: aie::neg(var) and check if we can introduce unsigned type
        neg_pattern = re.compile(
            r'(aie::neg\s*\(\s*)([a-zA-Z_]\w*)(\s*\))'
        )

        for match in neg_pattern.finditer(content):
            var_name = match.group(2)
            # Look for the variable's declaration to see if it's already signed
            # If we already have candidates from strategy 1 for this file, skip duplicates
            # This strategy: change the neg call to cast to unsigned first
            # e.g., aie::neg(var) -> aie::neg(var.cast_to<uint16>()) -- but simpler:
            # We can also just add a candidate that changes the neg argument type annotation
            pass  # Strategy 1 is sufficient for most cases

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
