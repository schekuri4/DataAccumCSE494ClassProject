import re
import copy

BUG_FAMILY = {
    "family_id": "BF214",
    "bug_type": "concat_exceeds_max_register_size",
    "category": "vector_shuffles_and_permutations",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::concat",
        "aie::vector<int32,32>",
        "aie::vector<int32,16>"
    ],
    "mutation_strategy": "Use aie::concat to combine vectors whose total lane count exceeds the maximum supported register width (e.g., concat two 32-lane int32 vectors yielding 64 lanes = 2048 bits, which exceeds 1024-bit max on AIE-ML or is unsupported).",
    "repair_expectation": "Reduce the input vector sizes so the concatenated result fits within the architecture's maximum vector register size (1024 bits for AIE1, 512/1024 for AIE-ML).",
    "validation_signal": "WSL Vitis/AIE compile failure with unsupported vector size or no matching overload for concat.",
    "tags": [
        "architecture",
        "concat",
        "max_size",
        "register_overflow",
        "vector_shuffles_and_permutations"
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

        # Strategy 1: Find existing aie::concat calls with two int32,16 vectors
        # and mutate the vector declarations to int32,32 so concat produces 64 lanes
        # Pattern: aie::concat(varA, varB) where varA and varB are aie::vector<int32,16>
        
        # Look for aie::vector<int32,16> declarations and upgrade them to 32
        vec16_decl_pattern = re.compile(
            r'(aie::vector\s*<\s*int32\s*,\s*)16(\s*>)'
        )
        
        # Check if file has aie::concat usage
        has_concat = 'aie::concat' in content
        
        if has_concat:
            # Find all int32,16 vector declarations and mutate them to int32,32
            for match in vec16_decl_pattern.finditer(content):
                original = match.group(0)
                replacement = match.group(1) + '32' + match.group(2)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "concat_exceeds_max_register_size",
                    "category": "vector_shuffles_and_permutations",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        "Changed aie::vector<int32,16> to aie::vector<int32,32> so that "
                        "aie::concat of two such vectors produces 64 lanes (2048 bits), "
                        "exceeding the maximum supported register width."
                    )
                })

        # Strategy 2: Find aie::concat calls with int32,16 arguments and change
        # the concat result type or add an extra argument
        # Pattern: aie::concat(a, b) -> aie::concat(a, b, a) or similar
        concat_call_pattern = re.compile(
            r'(aie::concat\s*\(\s*)(\w+)\s*,\s*(\w+)(\s*\))'
        )

        if has_concat:
            for match in concat_call_pattern.finditer(content):
                original = match.group(0)
                # Duplicate one argument to triple the size
                arg1 = match.group(2)
                arg2 = match.group(3)
                replacement = match.group(1) + arg1 + ', ' + arg2 + ', ' + arg1 + ', ' + arg2 + match.group(4)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "concat_exceeds_max_register_size",
                    "category": "vector_shuffles_and_permutations",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        "Added extra arguments to aie::concat so the total lane count "
                        "exceeds the maximum supported register width (e.g., 4x16=64 lanes "
                        "of int32 = 2048 bits)."
                    )
                })

        # Strategy 3: If file has aie::vector<int32,32> already and concat,
        # this is already potentially buggy but let's also find cases where
        # we can introduce a concat of two 32-lane vectors
        vec32_decl_pattern = re.compile(
            r'(aie::vector\s*<\s*int32\s*,\s*32\s*>)')

        if has_concat:
            # Already covered by strategy 2 adding extra args
            pass
        else:
            # If there's no concat but there are vector declarations, introduce one
            # Find pairs of vector<int32,32> variable declarations
            vec32_decls = list(vec32_decl_pattern.finditer(content))
            if len(vec32_decls) >= 1:
                # Find a suitable place to insert a concat - after a vector declaration line
                line_pattern = re.compile(
                    r'([ \t]*)(aie::vector\s*<\s*int32\s*,\s*32\s*>\s+(\w+)\s*[^;]*;)'
                )
                matches_list = list(line_pattern.finditer(content))
                if len(matches_list) >= 1:
                    m = matches_list[0]
                    var_name = m.group(3)
                    indent = m.group(1)
                    original_line = m.group(0)
                    # Add a concat line after the declaration
                    concat_line = (
                        f"\n{indent}auto concat_result = aie::concat({var_name}, {var_name});"
                        f"  // 64 lanes of int32 = 2048 bits - exceeds max register"
                    )
                    replacement_line = original_line + concat_line
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "concat_exceeds_max_register_size",
                        "category": "vector_shuffles_and_permutations",
                        "start": m.start(),
                        "end": m.end(),
                        "original": original_line,
                        "replacement": replacement_line,
                        "description": (
                            "Inserted aie::concat of two 32-lane int32 vectors, producing "
                            "64 lanes (2048 bits) which exceeds the maximum register width."
                        )
                    })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
